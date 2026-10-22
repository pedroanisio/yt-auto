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
import copy
import csv
import json
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import claude_engine
from claude_engine import ClaudeError
from .core import Idea, read_json, timecode, wrap, write_json
from .ideas import CHANNEL_STYLES, style_brief
from . import flags, harness, house, narrative, spend, workspace

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

#: How a section joins the one before it (docs/narrative structure 1: the
#: honest connective between beats is BUT or THEREFORE, never AND THEN).
#: `open` is section 1's alone; `and` is listed so a writer can confess it
#: and a pre-check can flag it, rather than dress an AND up as a BUT.
CONNECTIVES = ("open", "and", "but", "therefore", "because")

#: Who the film is pitched at; it decides what a writer defines.
AUDIENCES = ("lay", "enthusiast", "expert")

#: What a shot does in the story. `qa` samples every rehook and payoff shot,
#: `pilot` keeps the hook and the payoff, and `build` hands the editor the
#: payoff shot; any other value is body.
FUNCTIONS = ("hook", "context", "setup", "reveal", "rehook", "payoff", "handoff")

#: How a value outside one of these lists is treated: the harness asks again
#: once, then the stage records it (a `note:` in script.json's `findings`)
#: and reads it as no role at all. Stated in the schema, so the model is told.
_ENUM_DRIFT = ("A value outside this list is asked again once, then recorded as a "
               "note and treated as having no role.")

#: An empty string passes the schema (only presence is checked); the plan's
#: deterministic pre-checks catch it, so the description says so.
_NOT_EMPTY = "Never empty: an empty value fails the plan's pre-check."

#: The film-wide story fields of a plan, shared by the plan and by the
#: `plan-fields` decision `--outline` asks over the research's outline.
STORY_PROPERTIES = {
    "promise": {"type": "string",
                "description": "what the first fifteen seconds promise the viewer"},
    "payoff": {"type": "string", "description": "how that promise is paid off"},
    "train": {"type": "string", "description": (
        "One sentence: who or what pursues what, against which named obstacle, "
        "and the question the film answers (a question mark, or a whether/why/how "
        "clause). Not a topic. " + _NOT_EMPTY)},
    "stakes": {"type": "string", "description": (
        "the concrete consequence if the obstacle wins. " + _NOT_EMPTY)},
    "viewer_stake": {"type": "string", "description": (
        "why it matters to the viewer, addressed to them. " + _NOT_EMPTY)},
    "misconception": {"type": "string", "description": (
        "the intuitive but wrong belief the film breaks, led by the research's "
        "misconception id (\"[M1] ...\") when the research lists one; empty "
        "when there is none, with the reason in misconception_why")},
    "misconception_why": {"type": "string",
                          "description": "why misconception is empty, or empty"},
    "audience": {"type": "string", "enum": list(AUDIENCES),
                 "description": "who the film is pitched at. " + _ENUM_DRIFT},
    "reveal": {
        "type": "object",
        "properties": {
            "section": {"type": "integer",
                        "description": "the section number holding the main reveal"},
            "constraint_section": {"type": "integer", "description": (
                "the earlier section that shows why the reveal is hard")},
        },
        "required": ["section", "constraint_section"],
    },
    "questions": {
        "type": "array",
        "description": "the ledger: every question the film raises and where it is answered",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Q1, Q2, ..."},
                "text": {"type": "string"},
                "opened_in": {"type": "integer", "description": "section number"},
                "closed_in": {"type": "integer", "description": (
                    "the section that answers it, the same as opened_in or a later "
                    "one; 0 for a question deliberately left open, with why in "
                    "left_open_why")},
                "left_open_why": {"type": "string", "description": (
                    "why a question with closed_in 0 is left open on purpose; "
                    "empty otherwise")},
            },
            "required": ["id", "text", "opened_in", "closed_in"],
        },
    },
    "ending": {
        "type": "object",
        "properties": {
            "payoff_line": {"type": "string", "description": (
                "the last narrated line: the title's promise, paid in resolved form")},
            "callback": {"type": "string",
                         "description": "how the ending mirrors the opening"},
            "next_film": {"type": "string", "description": (
                "one named film that continues the question")},
        },
        "required": ["payoff_line", "callback", "next_film"],
    },
}
STORY_REQUIRED = ["promise", "payoff", "train", "stakes", "viewer_stake", "audience",
                  "reveal", "questions", "ending"]

#: What each section carries for the story, beside its beats.
SECTION_STORY = {
    "opens": {"type": "array", "items": {"type": "string"},
              "description": "ids of the questions this section raises"},
    "closes": {"type": "array", "items": {"type": "string"},
               "description": "ids of the questions this section answers"},
    "connective": {"type": "string", "enum": list(CONNECTIVES), "description": (
        "how this section joins the one before: open for section 1 only, then "
        "but or therefore (because when it explains); and only when nothing "
        "else is honest. " + _ENUM_DRIFT)},
    "rehook": {"type": "string", "description": (
        "the id of a question this section re-opens to hold the viewer, or empty")},
    "escalation": {"type": "string",
                   "description": "one clause: what gets harder or costlier here"},
    "anchor_visual": {"type": "string",
                      "description": "the one picture this section stands on"},
}
SECTION_STORY_REQUIRED = ["opens", "closes", "connective", "anchor_visual"]

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "logline": {"type": "string"},
        **STORY_PROPERTIES,
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
                    **SECTION_STORY,
                },
                "required": ["heading", "purpose", "seconds", "beats",
                             *SECTION_STORY_REQUIRED],
            },
        },
    },
    "required": ["logline", *STORY_REQUIRED, "sections"],
}

#: The story fields over an outline the research already wrote: its sections
#: are fixed, so each answer row is one of them, in order.
PLAN_FIELDS_SCHEMA = {
    "type": "object",
    "properties": {
        **STORY_PROPERTIES,
        "sections": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"heading": {"type": "string"}, **SECTION_STORY},
                      "required": list(SECTION_STORY_REQUIRED)},
        },
    },
    "required": [*STORY_REQUIRED, "sections"],
}


def section_schema(sourced: bool) -> dict:
    """Shots for one section. `sourced` makes the citation mandatory."""
    props = {
        "visual": {"type": "string"},
        "narration": {"type": "string"},
        "on_screen": {"type": "string"},
        "on_screen_text": {"type": "array", "items": {"type": "string"}},
        "asset": {"type": "string"},
        "style": {"type": "string"},
        "source": {"type": "string"},
        "seconds": {"type": "integer"},
        "note": {"type": "string"},
        "function": {"type": "string", "enum": list(FUNCTIONS),
                     "description": "what the shot does in the story. " + _ENUM_DRIFT},
        "shows": {"type": "string", "description": (
            "one clause: what the picture demonstrates of this line's claim")},
    }
    required = ["visual", "narration", "seconds", "asset", "style", "function", "shows"]
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

REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "action": {"type": "string", "enum": ["fix", "keep"]},
                    "narration": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["n", "action", "narration", "why"],
            },
        }
    },
    "required": ["fixes"],
}

#: `revise` over the narration rules: a rule can measure the on-screen text
#: (legibility, an unspoken figure), so the fix may change it too.
RULE_REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **REVISE_SCHEMA["properties"]["fixes"]["items"]["properties"],
                    "on_screen_text": {"type": "array", "items": {"type": "string"},
                                       "description": (
                                           "the shot's on-screen strings after the fix, "
                                           "read only for a rule that measured the "
                                           "screen (rule:legibility, "
                                           "rule:unspoken-number); omit to leave them "
                                           "as they are")},
                },
                "required": ["n", "action", "narration", "why"],
            },
        }
    },
    "required": ["fixes"],
}

#: `revise` over lines sent back (`yt script --revise`): QA's script owner
#: answers for the supers too, so a reason about the screen may change them.
BACK_REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **REVISE_SCHEMA["properties"]["fixes"]["items"]["properties"],
                    "on_screen_text": {"type": "array", "items": {"type": "string"},
                                       "description": (
                                           "the shot's on-screen strings after the fix, "
                                           "only when its reason is about what the "
                                           "screen shows; omit to leave them as they "
                                           "are")},
                },
                "required": ["n", "action", "narration", "why"],
            },
        }
    },
    "required": ["fixes"],
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
    on_screen: str = ""     # direction for the super: where, how, when
    on_screen_text: list[str] = field(default_factory=list)  # what it reads
    asset: str = ""
    style: str = ""
    source: str = ""
    seconds: int = 0
    note: str = ""
    n: int = 0          # global shot number, for polish and fact-check notes
    label: str = ""     # "3.2", for humans reading the page
    at: int = 0         # start time in seconds from the top
    revised: str = ""   # why the polish pass rewrote this line
    function: str = ""  # its role in the story (FUNCTIONS); "" before AUTO-90
    shows: str = ""     # what the picture demonstrates of the line's claim

    @classmethod
    def from_raw(cls, raw: dict) -> Shot:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in known and v is not None}
        for key in ("visual", "narration", "on_screen", "asset", "style",
                    "source", "note", "function", "shows"):
            if key in clean:
                clean[key] = str(clean[key]).strip()
        if "on_screen_text" in clean:
            given = clean["on_screen_text"]
            clean["on_screen_text"] = [
                " ".join(str(t).split())
                for t in (given if isinstance(given, (list, tuple)) else [given])
                if str(t).strip()]
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
    # The plan's story fields for this section (SECTION_STORY); empty in a
    # screenplay written before AUTO-90.
    opens: list[str] = field(default_factory=list)
    closes: list[str] = field(default_factory=list)
    connective: str = ""
    rehook: str = ""
    escalation: str = ""
    anchor_visual: str = ""

    @classmethod
    def from_spec(cls, raw: dict, shots: list[Shot] | None = None,
                  heading: str = "") -> Section:
        """A section from its plan entry or its JSON form, story fields included."""
        ids = lambda key: [str(q) for q in raw.get(key) or []  # noqa: E731
                           if str(q).strip()]
        return cls(heading=str(raw.get("heading") or heading),
                   purpose=str(raw.get("purpose") or ""),
                   seconds=int(raw.get("seconds", 0) or 0),
                   beats=[str(b) for b in raw.get("beats") or []],
                   shots=list(shots or []),
                   opens=ids("opens"), closes=ids("closes"),
                   connective=str(raw.get("connective") or "").strip(),
                   rehook=str(raw.get("rehook") or "").strip(),
                   escalation=str(raw.get("escalation") or "").strip(),
                   anchor_visual=str(raw.get("anchor_visual") or "").strip())

    @property
    def words(self) -> int:
        return sum(shot.words for shot in self.shots)

    @property
    def actual_seconds(self) -> int:
        return sum(shot.seconds for shot in self.shots)


#: How a silent shot is listed to the review stages. It is a mark in the
#: listing, never narration: a model that hands it back as a shot's line means
#: the shot is silent, and a voice engine handed it records nothing (measured:
#: ElevenLabs strips the brackets and refuses the empty text).
SILENT = "[picture only, no narration]"


def spoken(line: str) -> str:
    """The words of a narration line: "" for a line that is only a bracketed
    direction (the SILENT mark, or "[no narration]" and the like)."""
    text = " ".join(str(line or "").split())
    if text.startswith("[") and text.endswith("]") and "]" not in text[:-1]:
        return ""
    return text


def shot_listing(shots: list[Shot], *, cite: bool = False, supers: bool = False) -> str:
    """Every shot numbered for the review stages.

    Silent shots are listed too, marked: dropping them leaves gaps in the
    numbering, and a reviewer reads a gap as a missing shot. `supers` adds
    what each shot puts on screen, for a check that measures it.
    """
    lines = []
    for shot in shots:
        body = shot.narration or SILENT
        if cite and shot.source:
            body += f"  [cited: {shot.source}]"
        if supers and shot.on_screen_text:
            body += "  [on screen: " + " | ".join(shot.on_screen_text) + "]"
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


def _figure_key(token: str) -> str:
    return " ".join(token.lower().split())


def figures_in(text: str) -> list[str]:
    """The figures a line says, in order, each once, as `spoken_numbers` sees them."""
    seen, out = set(), []
    for match in _NUMBER.findall(text or ""):
        token = match.strip()
        key = _figure_key(token)
        if len(token) > 1 and key not in seen:
            seen.add(key)
            out.append(token)
    return out


class Ledger:
    """Which section owns each figure, shared by sections drafted side by side.

    Sections written concurrently cannot read what the others said, so a
    figure is reserved up front for the first section whose plan names it;
    the others are told to leave it alone.
    """

    def __init__(self) -> None:
        self._owner: dict[str, int] = {}
        self._shown: dict[str, str] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_plan(cls, specs: list[dict]) -> Ledger:
        ledger = cls()
        for index, spec in enumerate(specs, 1):
            text = " . ".join([*map(str, spec.get("beats") or []),
                               *map(str, spec.get("claims") or [])])
            for token in figures_in(text):
                ledger.reserve(token, index)
        return ledger

    def reserve(self, figure: str, section: int) -> int:
        """Claim a figure for a section unless another holds it; the owner."""
        key = _figure_key(figure)
        with self._lock:
            self._shown.setdefault(key, figure)
            return self._owner.setdefault(key, section)

    def owner(self, figure: str) -> int | None:
        with self._lock:
            return self._owner.get(_figure_key(figure))

    def reserved_from(self, section: int) -> list[str]:
        """Figures held by other sections."""
        with self._lock:
            return [self._shown[k] for k, o in self._owner.items() if o != section]

    def owned_by(self, section: int) -> list[str]:
        with self._lock:
            return [self._shown[k] for k, o in self._owner.items() if o == section]


def repeated_figures(sections: list[Section]) -> list[dict]:
    """Figures spoken in more than one section: the later shot, and the label
    of the shot that said the figure first."""
    first: dict[str, tuple[int, str]] = {}
    out = []
    for index, section in enumerate(sections):
        for shot in section.shots:
            for token in figures_in(shot.narration):
                key = _figure_key(token)
                if key not in first:
                    first[key] = (index, shot.label)
                elif first[key][0] != index:
                    out.append({"figure": token, "n": shot.n, "label": shot.label,
                                "first": first[key][1]})
    return out


# --------------------------------------------------------------------------- #
# research and direction inputs
# --------------------------------------------------------------------------- #

def sections_of(data: dict) -> list[Section]:
    """A screenplay's sections of shots, from its JSON form."""
    known = {f.name for f in fields(Shot)}
    sections = []
    for raw in (data or {}).get("sections", []):
        sections.append(Section.from_spec(raw, [
            Shot(**{**{k: v for k, v in shot.items() if k in known},
                    "narration": spoken(shot.get("narration", ""))})
            for shot in raw.get("shots", [])]))
    return sections


def load_script(path: str) -> tuple[list[Section], dict]:
    """The screenplay `yt script` wrote, as sections of shots."""
    data = read_json(path) or {}
    return sections_of(data), data


#: Claims are offered best-sourced first when there are more than fit.
_CONFIDENCE = {"high": 0, "medium": 1, "low": 2}


def misconception_line(item) -> str:
    """One misconception from the dossier: `{belief, source}` since AUTO-90,
    a bare string before."""
    if isinstance(item, dict):
        belief = " ".join(str(item.get("belief") or "").split())
        source = " ".join(str(item.get("source") or "").split())
        return f"{belief} (source: {source})" if source else f"{belief} (no source)"
    return " ".join(str(item).split())


def identifiable_case(data: dict) -> str:
    """The named person, place or event the research found for the film to
    follow, with its source; "" when it found none."""
    case = data.get("identifiable_case") or (data.get("outline") or {}).get(
        "identifiable_case")
    if not isinstance(case, dict) or not str(case.get("who") or "").strip():
        return ""
    who = " ".join(str(case["who"]).split())
    source = " ".join(str(case.get("source") or "").split())
    return f"{who} (source: {source})" if source else f"{who} (no source)"


def research_block(data: dict, *, limit: int = 60) -> str:
    """Claims with their sources, licensed assets, and what to correct."""
    parts = []
    if data.get("premise"):
        parts.append(f"Researched premise: {data['premise']}")
    if data.get("verdict"):
        parts.append(f"Does it hold up: {data['verdict']}")

    # The claims the outline is built on always go in: dropped, the writer
    # loses the support for a beat it must write, and the fact-check flags
    # the line as unsourced when the dossier holds its source. The other
    # slots go to the best sourced of the rest.
    def key(text: str) -> str:
        return " ".join(str(text).lower().split())[:120]

    used = {key(c) for section in (data.get("outline") or {}).get("sections", [])
            for c in section.get("claims_used", [])}
    everything = data.get("claims", [])
    on_outline = [c for c in everything if key(c.get("claim", "")) in used]
    ranked = sorted((c for c in everything if key(c.get("claim", "")) not in used),
                    key=lambda c: _CONFIDENCE.get(c.get("confidence"), 3))
    if len(ranked) > limit:
        # Dropping claims silently would let the script contradict research
        # it never saw; say so, and drop the least trusted.
        print(f"note: {len(everything)} claims in the dossier; the script sees "
              f"the {len(on_outline)} the outline uses and the {limit} best "
              "sourced of the rest", file=sys.stderr)
    claims = []
    for claim in on_outline + ranked[:limit]:
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
        # Numbered, so the plan cites one ("[M2] ...") rather than invents it.
        parts.append("Misconceptions to correct out loud, with where people are "
                     "documented believing them:\n"
                     + "\n".join(f"- M{n}: {misconception_line(m)}"
                                 for n, m in enumerate(data["misconceptions"][:8], 1)))
    case = identifiable_case(data)
    if case:
        parts.append(f"An identifiable case the film can follow: {case}")
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


#: What the plan asks for beyond the beats, in the craft corpus's terms
#: (docs/narrative: judging 13, structure 1, 7, 8, 14, 22, 36, attention 7,
#: 9-10). The same words go to the plan and to `--outline`'s plan fields.
STORY_BRIEF = (
    "Plan it as a story the viewer follows, not a list of facts in order.\n"
    "- train: one sentence saying who or what pursues what, against which "
    "named obstacle, and the question the film answers. A sentence that names "
    "only a topic is not a train.\n"
    "- promise: what the first fifteen seconds promise the viewer; payoff: how "
    "that promise is paid off.\n"
    "- stakes: the concrete consequence if the obstacle wins; viewer_stake: "
    "why it matters to the viewer, addressed to them.\n"
    "- misconception: the belief the film breaks, the intuitive but wrong one "
    "a viewer arrives with. Lead it with the research's id (\"[M1] ...\") when "
    "the research lists it; never invent one. When there is none, leave it "
    "empty and say why in misconception_why.\n"
    "- audience: lay, enthusiast or expert.\n"
    "- reveal: the section holding the main reveal, and constraint_section, "
    "the earlier section that shows the viewer why it is hard. The reveal "
    "comes after the constraint, never before.\n"
    "- questions: the ledger of every question the film raises: an id (Q1, "
    "Q2, ...), the question, the section that opens it and the section that "
    "answers it (the same section or a later one). Every question opened is "
    "answered, or is marked left open (closed_in 0) with why in "
    "left_open_why, as a choice; a question that points past the film belongs "
    "in ending.next_film.\n"
    "- ending: payoff_line, the last narrated line, which pays the promise in "
    "resolved form; callback, how it mirrors the opening; next_film, one "
    "named film that continues the question.\n"
    "For each section also give: opens and closes (question ids); connective, "
    "how it joins the section before it: open for section 1 only, then BUT "
    "(it turns against what came before) or THEREFORE (it follows from it), "
    "because when it explains why; and when nothing else is honest, so it can "
    "be seen; rehook, the id of a question it re-opens to hold the viewer, or "
    "empty; escalation, one clause on what gets harder or costlier; and "
    "anchor_visual, the one picture it stands on.\n"
    "Every plan field is crew-facing: write them in English, like the shot "
    "descriptions, whatever language the film is in, so the later stages can "
    "read them.")

#: Told to the plan when the idea carries a promise (`yt ideas --rich`): the
#: packaging is fixed before the hook is written, and the hook answers to it
#: (docs/narrative: structure 5, 6). The plan's own promise keeps the one
#: meaning STORY_BRIEF gives it, what the first fifteen seconds promise; it
#: must keep to the packaging's, and is still the model's to write: nothing
#: copies the idea's over it.
FROZEN_PROMISE = (
    "The promise above is frozen: the title and thumbnail were fixed before "
    "this plan. The plan's promise, what the first fifteen seconds promise, "
    "must keep to it: the hook delivers what the title and thumbnail promise. "
    "Restate its question in the first 30 seconds and pay it off in the last "
    "section; do not change what the title promises to suit the plan.")

#: What the writer of every section is told about the audience.
AUDIENCE_BRIEF = {
    "lay": ("The audience is lay: define every term the first time it is said, "
            "in plain words, and say the misconception out loud before you "
            "break it."),
    "enthusiast": ("The audience is enthusiasts: they know the field's common "
                   "terms, so define only the specialist ones."),
    "expert": ("The audience is expert: skip the basics and the definitions they "
               "already hold, and spend the words on what is new or contested."),
}

#: What each connective asks of the section that carries it.
CONNECTIVE_BRIEF = {
    "open": "It opens the film.",
    "and": ("The plan joins it to the section before with AND: it adds, it does "
            "not turn or follow. Make the join a consequence or a turn if the "
            "material honestly allows."),
    "but": "It joins the section before with BUT: it turns against what that section established.",
    "therefore": ("It joins the section before with THEREFORE: it follows from "
                  "what that section established, as its consequence."),
    "because": "It joins the section before with BECAUSE: it explains why what came before happened.",
}


def story_lines(plan: dict) -> list[str]:
    """The film's story, as every section's writer is handed it: the train,
    the stakes, the belief it breaks and who it is pitched at."""
    rows = [(label, " ".join(str(plan.get(key) or "").split()))
            for key, label in (("train", "The film's train"),
                               ("stakes", "What is at stake"),
                               ("viewer_stake", "Why the viewer cares"),
                               ("misconception", "The belief the film breaks"))]
    out = ["\n".join(f"{label}: {text}" for label, text in rows if text)]
    out.append(AUDIENCE_BRIEF.get(str(plan.get("audience") or ""), ""))
    return [p for p in out if p]


def _question(plan: dict, qid: str) -> str:
    for q in plan.get("questions") or []:
        if isinstance(q, dict) and str(q.get("id", "")) == qid:
            return f"{qid} ({' '.join(str(q.get('text') or '').split())})"
    return qid


def section_story_lines(spec: dict, plan: dict, *, index: int, total: int) -> list[str]:
    """One section's part of the story: the questions it opens and answers,
    how it joins the one before, what it re-opens, what escalates, the
    picture it stands on, its place around the reveal and, for the last
    section, the ending the plan locked."""
    bits = []
    for key, verb in (("opens", "This section opens"), ("closes", "This section answers")):
        ids = [str(q) for q in spec.get(key) or [] if str(q).strip()]
        if ids:
            bits.append(f"{verb}: " + "; ".join(_question(plan, q) for q in ids))
    if CONNECTIVE_BRIEF.get(str(spec.get("connective") or "")):
        bits.append(CONNECTIVE_BRIEF[str(spec["connective"])])
    if str(spec.get("rehook") or "").strip():
        bits.append(f"Re-open {_question(plan, str(spec['rehook']).strip())} partway "
                    "through, so the viewer stays for its answer.")
    if str(spec.get("escalation") or "").strip():
        bits.append(f"What gets harder or costlier here: {spec['escalation']}")
    if str(spec.get("anchor_visual") or "").strip():
        bits.append(f"The picture this section stands on: {spec['anchor_visual']}")
    reveal = plan.get("reveal") if isinstance(plan.get("reveal"), dict) else {}
    if reveal.get("section") == index:
        bits.append("This section holds the film's main reveal"
                    + (f"; section {reveal['constraint_section']} showed why it is hard"
                       if reveal.get("constraint_section") else "") + ".")
    elif reveal.get("constraint_section") == index and reveal.get("section"):
        bits.append(f"This section shows why the reveal in section {reveal['section']} "
                    "is hard; do not give the reveal away.")
    ending = plan.get("ending") if isinstance(plan.get("ending"), dict) else {}
    if index == total and any(ending.get(k) for k in ("payoff_line", "callback", "next_film")):
        bits.append(
            "This is the last section. Its last narrated line pays the promise, "
            f"in the film's language: \"{ending.get('payoff_line', '')}\". It mirrors "
            f"the opening: {ending.get('callback', '')}. It hands off to one next "
            f"film: {ending.get('next_film', '')}.")
    return ["\n".join(bits)] if bits else []


# --------------------------------------------------------------------------- #
# the plan review (AUTO-92)
# --------------------------------------------------------------------------- #

#: Where `yt script` keeps the plan review that stopped it, beside the
#: script it did not write: script.json's existence marks the stage done
#: for `yt run`, so a stop cannot be recorded there. Holds the record, the
#: stage's engine report and its spend; a later run that gets past the plan
#: removes it, script.json (and its `plan_review`) then being the record.
PLAN_REVIEW_FILE = "plan_review.json"

#: The rubric rows a plan can be judged on before a line is written. The
#: hook and honesty are read in the narration, so they wait for it.
PLAN_CRITERIA = ("train", "stakes", "open_loops", "reveal_setup", "ending")

#: A film longer than this is held to at least one planned re-hook. The
#: number is judging 5's "films over seven minutes", from one leaked
#: production memo, and structure 23 is low confidence: a default to tune
#: from the channel's curves, so its pre-check is `house:`.
REHOOK_AFTER_SECONDS = 7 * 60

#: What makes a train a question when it has no question mark.
_ASKS = ("why", "how", "whether")

#: How the reviewer reads a plan, in the shape of styles.Director.critique:
#: the strongest objection first, then whether the plan still holds. Only
#: the train, the order, the sections that move nothing and the payoff are
#: grounds to send it back (judging 13; structure 3, 4).
PLAN_ATTACK = (
    "Attack this plan before any line of it is written. Name the single strongest "
    "objection to each of: its train (one sentence with a pursuer, a question and a "
    "named obstacle, or only a topic?), its order (could the sections be reordered "
    "without loss, a list rather than a chain?), each section's move (which sections "
    "advance the train not at all?) and its payoff (does it pay the promise with "
    "something the viewer can picture, or is it empty or generic?). Then say whether "
    "the plan still holds. accept: it does. revise: it is a story with concrete "
    "problems that planning again can fix; give each problem with its section (0 for "
    "the whole plan) and the fix. reject: it is not a story: the train names a "
    "topic, the sections are a list, or the payoff is empty. Only the train, the "
    "order, a section that moves nothing and the payoff are grounds to revise or "
    "reject. Leave the plan alone where an objection is weak, and do not send "
    "everything back to look rigorous.")

#: Said to the reviewer above the pre-checks: they are measured, not judged.
PRECHECK_NOTE = (
    "Pre-checks measured from the plan's own fields, before you read it. Confirm "
    "each in problems or say why it does not matter; none is a verdict on its own. "
    "A `house:` line is the house's own prior, never on its own a reason to revise "
    "or reject.")


def plan_view(plan: dict) -> dict:
    """The plan as the reviewer and a second plan read it: the story fields
    and each numbered section with its beats and story fields."""
    out = {k: plan[k] for k in ("logline", *STORY_PROPERTIES) if k in plan}
    out["sections"] = [
        {"section": n, **{k: spec[k] for k in ("heading", "purpose", "seconds", "beats",
                                               "claims", *SECTION_STORY) if k in spec}}
        for n, spec in enumerate(plan.get("sections") or [], 1) if isinstance(spec, dict)]
    return out


def plan_prechecks(plan: dict, seconds: int, title: str = "") -> list[str]:
    """What a plan's own fields show before anyone reads it, one line each:
    `rule:` for a corpus rule the fields break (an empty story field, an
    empty payoff line or one that names nothing the plan names, an AND
    join, a question with no answer, a reveal before its constraint) and
    `house:` for the house's priors (a long film with no re-hook, a train
    with no question). They go to the reviewer to confirm, never decide.

    The plan fields are crew-facing English (STORY_BRIEF), so the English
    stop words read them. "Names nothing the plan names" is the measurable
    half of structure 4's "generic": a payoff line sharing no content word
    with the train, the promise or the title is about nothing the film set
    up ("we learned a lot"). The `payoff` field is left out on purpose: the
    model writes the payoff line from it, so a shared word there shows
    nothing."""
    lex = LEXICON["en"]
    out: list[str] = []
    for key in ("train", "stakes", "viewer_stake"):
        if not " ".join(str(plan.get(key) or "").split()):
            out.append(f"rule: {key} is empty (judging 13; attention 8, 10)")
    ending = plan.get("ending") if isinstance(plan.get("ending"), dict) else {}
    line = " ".join(str(ending.get("payoff_line") or "").split())
    named = " ".join(str(plan.get(k) or "") for k in ("train", "promise")) + " " + title
    if not line:
        out.append("rule: the payoff line is empty: the ending is locked before any "
                   "section is written (structure 4)")
    elif not _found(_content(line, lex), named):
        out.append(f"rule: the payoff line names nothing the train, the promise or the "
                   f"title names, so it may be generic (\"{line}\"): it pays the "
                   "promise with a concrete fact or image (structure 4)")
    sections = [s for s in plan.get("sections") or [] if isinstance(s, dict)]
    for n, spec in enumerate(sections, 1):
        if str(spec.get("connective") or "").strip().lower() == "and":
            out.append(f"rule: section {n} joins the one before with AND: between beats "
                       "the honest connective is BUT or THEREFORE, and an AND join is a "
                       "list (structure 1, 3)")
    for q in plan.get("questions") or []:
        if not isinstance(q, dict):
            continue
        qid, closed = q.get("id") or "?", q.get("closed_in")
        if not isinstance(closed, int) or isinstance(closed, bool):
            out.append(f"rule: question {qid} has no closed_in: every question opened is "
                       "answered or left open on purpose (structure 8)")
        elif closed == 0 and not str(q.get("left_open_why") or "").strip():
            out.append(f"rule: question {qid} is left open with no left_open_why: a "
                       "question left open is a choice with a reason (structure 8)")
    reveal = plan.get("reveal") if isinstance(plan.get("reveal"), dict) else {}
    at, before = reveal.get("section"), reveal.get("constraint_section")
    if isinstance(at, int) and at > 0:
        if not isinstance(before, int) or before <= 0:
            out.append(f"rule: the reveal (section {at}) names no constraint section before "
                       "it (structure 22)")
        elif before >= at:
            out.append(f"rule: the constraint (section {before}) does not come before the "
                       f"reveal (section {at}): the viewer knows why it is hard first "
                       "(structure 22)")
    if seconds > REHOOK_AFTER_SECONDS and not any(str(s.get("rehook") or "").strip()
                                                  for s in sections):
        out.append(f"house: a film over seven minutes with no rehook in any section "
                   f"({timecode(seconds)}): plan one where the opening hands over and at "
                   "the later drop points (structure 23, judging 5)")
    train = " ".join(str(plan.get("train") or "").split())
    if train and "?" not in train and not _has(train, _ASKS):
        out.append("house: the train asks no question (no question mark, no why, how or "
                   "whether): a nudge, since punctuation alone satisfies it (judging 13)")
    return out


def review_field_lines(review) -> list[str]:
    """What a plan review's own fields found against the plan, one line
    each, so a second plan is told them even when the problems list is
    thin: the train, the order, the sections that move nothing, the payoff
    and the questions left unanswered."""
    if not isinstance(review, dict):
        return []
    out = []
    if review.get("train_ok") is False:
        out.append("the whole plan: the train is not one sentence with a question and a "
                   "named obstacle")
    if review.get("reorder_survives") is True:
        out.append("the whole plan: the sections could be reordered without loss, a list "
                   "rather than a chain")
    for n in review.get("sections_advancing_none") or []:
        out.append(f"section {n}: it moves the train not at all")
    if review.get("payoff_concrete") is False:
        out.append("the whole plan: the payoff does not pay the promise with something the "
                   "viewer can picture")
    unanswered = [str(q) for q in review.get("questions_unanswered") or []]
    if unanswered:
        out.append(f"the whole plan: question {', '.join(unanswered)} is opened and never "
                   "closed nor left open on purpose")
    return out


def problem_lines(problems) -> list[str]:
    """A plan review's problems, one line each, as the second plan and the
    operator read them."""
    out = []
    for p in problems or []:
        if not isinstance(p, dict):
            continue
        where = f"section {p['section']}" if p.get("section") else "the whole plan"
        fix = str(p.get("fix") or "").strip()
        out.append(f"{where}: {str(p.get('problem') or '').strip()}"
                   + (f" (fix: {fix})" if fix else ""))
    return out


# --------------------------------------------------------------------------- #
# the story review: the script read before voice is paid for (AUTO-93)
# --------------------------------------------------------------------------- #

#: Samples of the story review a round (judging 11: the majority of at least
#: three runs, never one). Each is its own decision, `review-r<round>-s<k>`,
#: shown the rubric's levels in its own order (judging 8).
STORY_SAMPLES = 3

#: Every story-review verdict, one JSON line each, under the work directory:
#: the calibration set's raw material (judging 10). `label` is null until a
#: person reads the script and labels it; work/ is disposable, so the file
#: is evidence to copy out.
REVIEW_LOG = "review-log.jsonl"

#: What fixes a flag on the plan itself (the train, the reveal's setup, the
#: whole script): a new plan, read again. A revision of lines cannot reach it.
REPLAN_FIX = "yt script --fresh --review"

#: When `--gate` is to be turned on: the review is a prior until it has been
#: measured against people (judging 9, N10).
GATE_WHEN = ("turn on once work/review-log.jsonl holds 20 labelled scripts and the "
             "seeded-bad fixtures fail under the live reviewer")

#: Judging 14: the cold reader knows nothing but the words. Its system holds
#: the mission and this role only: no house, no feedback notes; its prompt
#: holds the narration and the plan's question texts, never their answers.
COLD_ROLE = (
    "You are hearing a film's narration for the first time, the way a viewer "
    "does: the words below are everything you know about it. You did not write "
    "it and have read nothing behind it. Say what you understood from the words, "
    "not what you guess the writer meant. Where the narration never says why "
    "something matters, say so rather than supplying a reason of your own.")

COLD_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "premise": {"type": "string", "description": (
            "what the film is about and what it asks, in one or two sentences of "
            "your own, as the narration gave it to you")},
        "unexplained_terms": {"type": "array", "items": {"type": "string"},
                              "description": "words a viewer needs and is never told"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "a major claim the film makes"},
                    "why_care": {"type": "string", "description": (
                        "why a viewer should care about it, as the narration says; "
                        "empty when it never says")},
                },
                "required": ["claim", "why_care"],
            },
        },
        "answered": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question_id": {"type": "string"},
                               "shot_index": {"type": "integer", "description": (
                                   "the number of the shot where the narration answers it")}},
                "required": ["question_id", "shot_index"],
            },
        },
    },
    "required": ["premise", "unexplained_terms", "claims", "answered"],
}

#: Whether the premise a cold reader restated is the film's train.
MATCH_ROLE = (
    "You compare two statements of what a film is about: the one it was planned "
    "on and the one a first-time viewer gave after hearing only its narration. "
    "You judge only whether they name the same question and the same obstacle.")

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["match", "mismatch"], "description": (
            "match: the viewer's premise names the train's question and obstacle, "
            "in any words; mismatch: it names another question, only a topic, or "
            "no obstacle")},
        "difference": {"type": "string", "description": (
            "what the viewer missed or heard instead; empty on a match")},
    },
    "required": ["verdict", "difference"],
}

#: A story-review sample's decision name, and the sample (seed) it is.
_STORY_NAME = re.compile(r"^review-r\d+-s(\d+)(?:-again)?$")


def merge_flags(samples: list[dict]) -> list[dict]:
    """The valid samples' flags, one per shot, section and criterion: the
    most severe wording kept, with how many samples raised it."""
    rank = {s: i for i, s in enumerate(narrative.SEVERITIES)}
    merged: dict[tuple, dict] = {}
    for sample in samples:
        for f in sample.get("flags") or []:
            if not isinstance(f, dict):
                continue
            key = (int(f.get("n", 0) or 0), int(f.get("section", 0) or 0),
                   str(f.get("criterion", "")))
            have = merged.get(key)
            if have is None:
                merged[key] = dict(f, n=key[0], section=key[1], samples=1)
                continue
            have["samples"] += 1
            if rank.get(f.get("severity"), 9) < rank.get(have.get("severity"), 9):
                merged[key] = dict(f, n=key[0], section=key[1], samples=have["samples"])
    return [merged[k] for k in sorted(merged)]


def story_flag_line(f: dict) -> str:
    """One review flag as the writer and the operator read it."""
    where = (f"shot {f['n']}" if f.get("n") else
             f"section {f['section']}" if f.get("section") else "the whole script")
    fix = str(f.get("fix") or "").strip()
    return (f"{where} [{f.get('criterion', '')}, {f.get('severity', '')}]: "
            f"{str(f.get('problem') or '').strip()}" + (f" (fix: {fix})" if fix else ""))


def story_review_lines(record) -> list[str]:
    """The story review for script.md, beside the plan review and the
    fact-check: the verdict and its samples, the grades, the cold read, what
    was revised and redrafted, and what no revision can reach."""
    if not isinstance(record, dict) or not record:
        return []
    out = ["## Story review", ""]
    samples = record.get("samples") or []
    valid = [s for s in samples if s.get("valid")]
    rounds = len(record.get("rounds") or [])
    out.append(f"**{record.get('verdict', '?')}** -- the median of {len(valid)} valid "
               f"sample(s) of {len(samples)}, read by {record.get('model') or 'the reviewer'}"
               + (f", over {rounds} rounds" if rounds > 1 else "") + ".")
    if record.get("verdict") == "reject":
        out.append("A reject stops voice only under `--gate`.")
    out.append("")
    grades = record.get("grades") or {}
    if grades:
        out += ["| Criterion | Grade |", "|---|---|"]
        out += [f"| {k} | {grades[k]} |" for k in narrative.KEYS if grades.get(k)]
        out.append("")
    for s in samples:
        state = ("failed: " + str(s["failed"]) if s.get("failed") else
                 "valid" if s.get("valid") else
                 "not valid: " + "; ".join(s.get("unsettled") or []))
        out.append(f"- {s.get('name')}: {s.get('verdict') or '-'}"
                   + (" (asked again)" if s.get("again") else "") + f", {state}")
    if samples:
        out.append("")
    flagged = record.get("flags") or []
    if flagged:
        out += ["Flags:", ""] + [f"- {story_flag_line(f)}" for f in flagged] + [""]
    cold = record.get("cold_read") or {}
    if cold.get("premise"):
        out.append(f"Cold read, from the narration alone: \"{cold['premise']}\" -- "
                   f"{cold.get('match') or 'not matched'} against the train"
                   + (f": {cold['difference']}" if cold.get("difference") else "") + ".")
        if cold.get("unexplained_terms"):
            out.append("Unexplained terms: " + ", ".join(cold["unexplained_terms"]) + ".")
        if cold.get("unanswered"):
            out.append("Questions the reader never heard answered: "
                       + ", ".join(cold["unanswered"]) + ".")
        out.append("")
    elif cold.get("failed"):
        out += [f"Cold read failed: {cold['failed']}", ""]
    for r in record.get("revisions") or []:
        out.append(f"- revised {r.get('label')}: \"{r.get('before', '')}\" -> "
                   f"\"{r.get('after', '')}\" ({r.get('why', '')})")
    for r in record.get("redrafts") or []:
        out.append(f"- redrafted section {r.get('section')} ({r.get('name')}): "
                   f"{r.get('shots_before')} shot(s) became {r.get('shots_after')}")
    if record.get("previous_verdicts"):
        out.append("Read again after revising: " + ", ".join(record["previous_verdicts"])
                   + (" (the revision was put back)" if record.get("reverted") else ""))
    for f in record.get("unresolved") or []:
        out.append(f"- unresolved: {story_flag_line(f)} -- `{f.get('command', REPLAN_FIX)}`")
    out.append("")
    return out


class Screenwriter:
    """Plans the beat sheet, writes each section, then polishes and checks."""

    def __init__(self, idea: Idea, *, model: str = "sonnet", minutes: float = 10,
                 wpm: int = DEFAULT_WPM, sections: int = 0,
                 research: str = "", direction: str = "", outline: dict | None = None,
                 inspo: list[str] | None = None, notes: str = "",
                 lang: str = "", house: str = "",
                 house_digest: dict | None = None,
                 jobs: int = 4, draft_effort: str | None = "medium",
                 reviewer_model: str | None = None,
                 review_budget_usd: float | None = None, rounds: int = 2,
                 samples: int = STORY_SAMPLES, fresh: bool = False,
                 review_log: Path | str | None = None,
                 verbose: bool = True, root: Path | str | None = None) -> None:
        self.idea = idea
        self.root = root            # the project, for its open revision notes
        self.model = model
        self.seconds = max(1, round(minutes * 60))
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
        self.jobs = max(1, jobs)
        #: The revision rounds (`--rounds`) of the fact-check and of the
        #: story review, each counted on its own.
        self.rounds = max(0, rounds)
        #: The story review's samples a round, and where each verdict is
        #: appended for the calibration set (none: not logged).
        self.samples = max(1, samples)
        self.review_log = Path(review_log) if review_log else None
        self.fresh = fresh
        #: The plan review's record when it stopped the stage, for the
        #: command to keep where `yt run` does not read it as done.
        self.plan_review_stop: dict | None = None
        #: Drafting is most of the calls; the fact-check keeps the full effort.
        #: An effort the operator set for the whole run wins over this.
        self.draft_effort = draft_effort
        #: The stage's one door to the model; each question is a named decision.
        self.harness = harness.Harness(
            root or ".", "script", model=model, role=SYSTEM,
            house_digest=self.house_digest, fresh=fresh, verbose=verbose)
        self.usage = self.harness.usage
        #: What the stage recorded instead of deciding: a value the model gave
        #: outside a schema list, kept as written and read as no role.
        self.findings: list[str] = []
        #: Drafting at its own effort, on the same books as the fact-check.
        self._drafter = harness.Harness(
            root or ".", "script", model=model, role=SYSTEM, effort=draft_effort,
            house_digest=self.house_digest, fresh=fresh, verbose=verbose, usage=self.usage,
            decisions=self.harness.decisions, lock=self.harness.lock)
        #: The plan's reader (AUTO-92): another model family (the command
        #: refuses the author's own), its own cap a review, the plan's
        #: reviewer role and the house in its system; on the same books, so
        #: the stage's record bills it and each of its rows names its model.
        self.reviewer = harness.Harness(
            root or ".", "script", model=reviewer_model, role=narrative.reviewer_role(plan=True),
            house=house, house_digest=self.house_digest, budget_usd=review_budget_usd,
            fresh=fresh, verbose=verbose, usage=self.usage, decisions=self.harness.decisions,
            lock=self.harness.lock) if reviewer_model else None
        self.reviewer_model = reviewer_model
        self.review_budget_usd = review_budget_usd
        #: The story review's readers (AUTO-93), made when first asked: one
        #: per sample, each with the rubric's levels in its own order; the
        #: cold reader, with nothing in its system but the mission and its
        #: role; and the reader that matches its premise to the train.
        self._readers: dict[object, harness.Harness] = {}

    # -- plumbing ----------------------------------------------------------

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _reader(self, which: object) -> harness.Harness:
        """The story review's harness for sample `which` (an int seed),
        `cold` or `match`: the reviewer's model and books, its own role.
        The cold reader alone has no house and no feedback notes (judging
        14: it knows the words and nothing else)."""
        if which not in self._readers:
            cold = which == "cold"
            role = (COLD_ROLE if cold else MATCH_ROLE if which == "match"
                    else narrative.reviewer_role(seed=int(which)))
            self._readers[which] = harness.Harness(
                self.root or ".", "script", model=self.reviewer_model or self.model,
                role=role, house="" if cold else self.house,
                house_digest=self.house_digest, feedback=not cold,
                budget_usd=self.review_budget_usd, fresh=self.fresh,
                verbose=self.verbose, usage=self.usage,
                decisions=self.harness.decisions, lock=self.harness.lock)
        return self._readers[which]

    def _ask(self, name: str, prompt: str, schema: dict) -> dict:
        if schema is narrative.PLAN_REVIEW_SCHEMA:
            return self.reviewer.decide(name, prompt, schema)
        # The story review is routed by its decision's name, so a test that
        # answers in place of `_ask` sees every question the stage asks.
        story = _STORY_NAME.match(name)
        if story:
            return self._reader(int(story.group(1))).decide(name, prompt, schema)
        if name in ("cold-read", "cold-read-match"):
            return self._reader("cold" if name == "cold-read" else "match").decide(
                name, prompt, schema)
        drafting = ("shots" in schema.get("properties", {}) and self.draft_effort
                    and not claude_engine.default_for("effort"))
        return (self._drafter if drafting else self.harness).decide(name, prompt, schema)

    def _brief(self) -> str:
        bits = [f"Title: {self.idea.title}", f"Genre: {self.idea.genre}",
                f"Premise: {self.idea.description}"]
        if self.idea.hook:
            bits.append(f"Planned hook: {self.idea.hook}")
        if self.idea.keywords:
            bits.append(f"Search intent: {', '.join(self.idea.keywords)}")
        # What `yt ideas --rich` planned past the title (AUTO-100). An idea
        # saved without them briefs as before, so its cached decisions hold.
        if self.idea.question:
            bits.append(f"Planned question: {self.idea.question}")
        if self.idea.stakes:
            bits.append(f"Stakes: {self.idea.stakes}")
        if self.idea.viewer_belief:
            bits.append(f"What a typical viewer already believes: "
                        f"{self.idea.viewer_belief}")
        if self.idea.promise:
            bits.append(f"Promise (title and thumbnail, frozen): {self.idea.promise}")
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

    def _frozen(self) -> list[str]:
        """The frozen-promise clause, for an idea that carries a promise;
        nothing otherwise, so a plan from an older idea is asked as before."""
        return [FROZEN_PROMISE] if self.idea.promise else []

    def _sent_back(self, previous: dict, review: dict) -> str:
        """What a second plan is told: the plan it wrote and the reviewer's
        problems with it. The new plan is the model's to write (invariant 3):
        nothing here edits the old one."""
        return (harness.brief("The plan you wrote before", plan_view(previous))
                + "\n\nA story reviewer of another model read that plan and sent it "
                "back. Plan again, fixing each problem below and keeping what it did "
                "not object to:\n"
                + "\n".join(f"- {line}" for line in (
                    problem_lines(review.get("problems")) + review_field_lines(review)
                    or ["(the reviewer named no problem)"])))

    def plan(self, *, previous: dict | None = None, review: dict | None = None) -> dict:
        """The beat sheet. Given the `previous` plan and the `review` that
        sent it back, the plan asked once more, as `plan-2` (`plan-fields-2`
        over an outline), with the problems in the prompt."""
        again = [self._sent_back(previous, review or {})] if previous is not None else []
        if self.outline.get("sections"):
            return self._plan_from_outline(again)

        count = section_count(self.seconds, self.sections)
        self._say(f"[plan] {count} section(s) for {timecode(self.seconds)}")
        parts = [self._brief(), *self._context(),
                 f"Plan this video as {count} sections that total "
                 f"{self.seconds} seconds. Open on the strongest concrete "
                 "fact or image, never a definition. Give each section a "
                 "heading, the job it does in one clause (purpose), a runtime "
                 "in seconds, and 2-5 beats in the order they are said. Where "
                 "research is supplied, name the claims each section rests on "
                 "and the assets it shows. Put the weakest material last or "
                 "cut it. Also give the logline.",
                 STORY_BRIEF, *self._frozen(), *again]
        data = self._ask("plan-2" if again else "plan", "\n\n".join(parts), PLAN_SCHEMA)

        # The model's section runtimes rarely add up; hold the total.
        fit_sections(data.get("sections") or [], self.seconds)
        self._record_drift(data)
        return data

    def _plan_from_outline(self, again: list[str] | None = None) -> dict:
        """The research's outline as the beat sheet, with the story fields
        asked over it (`plan-fields`) rather than left blank: the outline
        holds the sections and their beats, not the train or the ledger."""
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
        outline = [{"section": n, "heading": s["heading"], "seconds": s["seconds"],
                    "beats": s["beats"]} for n, s in enumerate(sections, 1)]
        parts = [self._brief(), *self._context(),
                 harness.brief("The research's outline, whose sections are fixed",
                               outline),
                 "Write the story fields for this outline. Answer one entry "
                 f"under sections for each of its {len(sections)} sections, in "
                 "its order.",
                 STORY_BRIEF, *self._frozen(), *(again or [])]
        prompt = "\n\n".join(parts)
        name = "plan-fields-2" if again else "plan-fields"
        fields_ = self._ask(name, prompt, PLAN_FIELDS_SCHEMA)
        rows = fields_.get("sections") or []
        # The count is part of the answer's validity, which the schema cannot
        # state: asked once more with the problem named (invariant 3), under
        # its own name so both answers are kept and a rerun replays them.
        if len(rows) != len(sections):
            problem = (f"you answered {len(rows)} section(s) for an outline of "
                       f"{len(sections)}")
            self._say(f"[plan] {name}: asking again ({problem})")
            fields_ = self._ask(
                f"{name}-retry",
                prompt + f"\n\nYour previous answer was rejected: {problem}. Answer "
                f"exactly {len(sections)} entries under sections, one per outline "
                "section, in its order.", PLAN_FIELDS_SCHEMA)
            rows = fields_.get("sections") or []
        if len(rows) != len(sections):
            # Neither answer is usable; kept, they would fail every rerun.
            for kept in (name, f"{name}-retry"):
                self.harness.forget(kept)
            raise ClaudeError(f"the plan fields answered {len(rows)} section(s) for "
                              f"an outline of {len(sections)}, after asking again")
        for section, row in zip(sections, rows):
            section.update({k: row[k] for k in SECTION_STORY if k in row})
        data = {"logline": self.outline.get("working_title", self.idea.title),
                **{k: fields_[k] for k in STORY_PROPERTIES if k in fields_},
                "sections": sections}
        self._record_drift(data)
        return data

    def _record_drift(self, plan: dict) -> None:
        """A list value the model kept after the harness asked again is the
        stage's to record (invariant 3): a `note:` naming it, the value kept
        as written and read as no role."""
        audience = plan.get("audience")
        if audience is not None and audience not in AUDIENCES:
            self.findings.append(f"note: audience {audience!r} not in the schema; "
                                 "no audience brief is given")
        for n, section in enumerate(plan.get("sections") or [], 1):
            connective = section.get("connective")
            if connective is not None and connective not in CONNECTIVES:
                self.findings.append(f"note: section {n}: connective {connective!r} "
                                     "not in the schema; no connective is asked for")

    # -- stage one and a half: the plan is read before it is paid for ------

    def review_plan(self, plan: dict, *,
                    name: str = "plan-review") -> tuple[dict, list[str], dict]:
        """One reading of the plan by the reviewer (`plan-review`): the plan,
        the rubric rows a plan shows, the attack and the pre-checks measured
        from its fields. A review whose fields contradict its verdict
        (narrative.check_plan_review) is asked once more, as `<name>-again`,
        with those lines; the second answer stands whatever it says.
        Returns the review, the pre-checks and what the again ask found
        (`again`, and the `unsettled` lines still standing). A failed call
        raises: the gate is never skipped in silence."""
        checks = plan_prechecks(plan, self.seconds, self.idea.title)
        self._say(f"[plan] {name}: {self.reviewer.model} reads the plan"
                  + (f" ({len(checks)} pre-check(s))" if checks else ""))
        parts = [self._brief(), harness.brief("The plan", plan_view(plan)),
                 "The criteria a plan can be judged on before a line is written:\n"
                 + narrative.rubric_text("reviewer", keys=PLAN_CRITERIA),
                 PLAN_ATTACK,
                 (PRECHECK_NOTE + "\n" + "\n".join(f"- {c}" for c in checks)) if checks
                 else "The pre-checks measured from the plan's fields found nothing."]
        prompt = "\n\n".join(parts)
        review = self._ask(name, prompt, narrative.PLAN_REVIEW_SCHEMA)
        contradictions = (narrative.check_plan_review(review)
                          if review.get("verdict") in narrative.PLAN_VERDICTS else [])
        if not contradictions:
            return review, checks, {"again": False, "unsettled": []}
        self._say(f"[plan] {name}: asking again ({'; '.join(contradictions)})")
        review = self._ask(
            f"{name}-again", prompt + "\n\nYour previous answer contradicted itself:\n"
            + "\n".join(f"- {line}" for line in contradictions)
            + "\nAnswer again. Keep the verdict and correct the fields, or change the "
            "verdict; the verdict is yours.", narrative.PLAN_REVIEW_SCHEMA)
        unsettled = (narrative.check_plan_review(review)
                     if review.get("verdict") in narrative.PLAN_VERDICTS else [])
        return review, checks, {"again": True, "unsettled": unsettled}

    def gate_plan(self, plan: dict, *, fix: bool = True) -> tuple[dict, dict]:
        """The plan review as a gate, before any section is drafted.

        accept goes on. revise asks the plan once more with the problems
        (`plan-2`) and reads it again (`plan-review-2`); under report-only
        (`fix` false) nothing is asked again and the plan goes on as it is.
        reject, or a second revise, raises before any write-NN, leaving the
        record on `plan_review_stop`. Only the reviewer's verdict decides:
        the pre-checks are for it to confirm, and a reading whose fields
        contradict its verdict is asked once more (`plan-review-again`),
        what still contradicts kept under `unsettled`. Returns the plan to
        draft and the record for script.json."""
        if self.reviewer is None:
            raise ValueError("the plan review needs a reviewer_model")
        rounds: list[dict] = []
        previous: dict | None = None
        while True:
            name = "plan-review" if previous is None else "plan-review-2"
            review, checks, asked = self.review_plan(plan, name=name)
            verdict = review.get("verdict")
            rounds.append({"name": name, "verdict": verdict,
                           "problems": list(review.get("problems") or []), **asked})
            if verdict not in narrative.PLAN_VERDICTS:
                # Asked again once by the harness and still off the list: no
                # verdict was given, so the stage fails rather than guess one.
                self.reviewer.forget(name)
                self.reviewer.forget(f"{name}-again")
                raise ClaudeError(f"the plan review answered verdict {verdict!r}, not one "
                                  f"of {', '.join(narrative.PLAN_VERDICTS)}")
            if verdict == "revise" and fix and previous is None:
                self._say(f"[plan] sent back with {len(review.get('problems') or [])} "
                          "problem(s); planning once more")
                # The drift notes so far were the first plan's; the second
                # plan files its own.
                self.findings.clear()
                previous, plan = plan, self.plan(previous=plan, review=review)
                continue
            break
        record = {"verdict": verdict, "problems": list(review.get("problems") or []),
                  "retried": previous is not None, "model": self.reviewer.model,
                  "prechecks": checks, "unsettled": rounds[-1]["unsettled"],
                  "rounds": rounds}
        if verdict == "reject" or (verdict == "revise" and fix):
            # script.json is not written on a stop (its existence marks the
            # stage done for `yt run`), so the command keeps this elsewhere.
            self.plan_review_stop = record
            said = "; ".join(problem_lines(record["problems"]) + review_field_lines(review)
                             ) or "no problem named"
            raise ClaudeError(
                f"the beat sheet did not pass review ({verdict}"
                + (" after planning once more" if record["retried"] else "")
                + f"): {said}. No section was drafted. Run `yt script` again with "
                "--notes saying what the plan must change; the reviews are kept in "
                f"{PLAN_REVIEW_FILE} and under decisions/script/")
        self._say(f"[plan] review: {verdict}")
        return plan, record

    # -- stage two: write it -----------------------------------------------

    def write_section(self, spec: dict, *, index: int, total: int,
                      plan: dict, written: list[Shot],
                      next_heading: str, sourced: bool,
                      previous: dict | None = None,
                      ledger: Ledger | None = None, name: str = "",
                      redraft: list[str] | None = None,
                      after: list[Shot] | None = None) -> list[Shot]:
        """One section's shots, asked as `write-NN`. A `redraft` is the
        story review sending a section back (`name` is `redraft-NN`): its
        flags are in the prompt, with how the section before ends (`written`)
        and how the one after ends (`after`), so the new section still
        joins them."""
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
        parts += story_lines(plan)
        parts.append(
            f"Write section {index} of {total}: \"{spec.get('heading', '')}\".\n"
            f"Its job: {spec.get('purpose', 'as the beats require')}\n"
            f"Its beats, in order:\n{beats}")
        parts += section_story_lines(spec, plan, index=index, total=total)
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
        elif previous:
            before = "; ".join(str(b) for b in previous.get("beats") or [])
            parts.append(f"The section before this one, \"{previous.get('heading', '')}\", "
                         "is being written at the same time as this one. It "
                         f"covers: {before or 'its own beats'}. Do not restate it.")
        if ledger is not None:
            others = ledger.reserved_from(index)
            if others:
                parts.append("Figures that belong to other sections. Do not say "
                             "any of them here: " + ", ".join(others))
            mine = ledger.owned_by(index)
            if mine:
                parts.append("Figures this section owns. Say each once; no "
                             "other section will: " + ", ".join(mine))
        if next_heading:
            parts.append(f"The next section is \"{next_heading}\". End this "
                         "one so that it is the obvious next thing, without "
                         "announcing it.")
        if after:
            tail = "\n\n".join(f'{s.label}: "{s.narration}"' for s in after[-2:])
            parts.append("The section after this one stays as written and ends on "
                         f"these shots; lead into it:\n\n{tail}")
        if redraft:
            parts.append("This section was written before, and a story reviewer of "
                         "another model sent it back with these problems. Write it "
                         "again so that each is fixed. You may restructure the "
                         "section; never add a fact:\n"
                         + "\n".join(f"- {line}" for line in redraft))

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
            "- on_screen: the direction for a super, label or lower third -- "
            "where it sits and how it moves -- or empty\n"
            "- on_screen_text: the exact strings a viewer reads on screen, one "
            "per string, with no direction in them; an empty list when "
            "nothing is shown\n"
            "- asset: the licensed asset it uses, or what has to be made\n"
            "- style: which visual treatment this shot belongs to\n"
            "- seconds: its duration\n"
            "- note: pacing, sound or performance direction, or empty\n"
            "- function: what the shot does in the story, one of "
            + ", ".join(FUNCTIONS) + " (rehook: it re-opens a question to hold "
            "the viewer; payoff: it pays the film's promise)\n"
            "- shows: one clause saying what the picture demonstrates of this "
            "line's claim: the picture proves the words, it does not decorate "
            "them")
        if sourced:
            parts.append("- source: the source backing this shot's claim, as "
                         "given in the research. Put the word UNSOURCED where "
                         "a line states something the research does not cover, "
                         "rather than attaching a source that does not say it.")

        data = self._ask(name or f"write-{index:02d}", "\n\n".join(parts),
                         section_schema(sourced))
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
            data = self._ask("polish", "\n\n".join(parts), POLISH_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[polish] failed ({exc}); keeping the script as written")
            return 0

        by_n = {s.n: s for s in shots}
        applied = 0
        for edit in data.get("edits", []):
            shot = by_n.get(int(edit.get("n", 0) or 0))
            line = spoken(edit.get("narration", ""))
            if shot is None or not line or line == shot.narration:
                continue
            shot.narration, shot.revised = line, str(edit.get("why", "")).strip()
            applied += 1
        self._say(f"[polish] rewrote {applied} line(s)")
        return applied

    # -- stage four: hold it to the research -------------------------------

    def verify(self, shots: list[Shot], *, name: str = "verify") -> dict:
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
            return self._ask(name, "\n\n".join(parts), CHECK_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[verify] failed ({exc})")
            return {}

    # -- stage five: fix what the check found ------------------------------

    def revise(self, shots: list[Shot], flags: list[dict], *,
               name: str = "revise", rules: bool = False, back: str = "",
               flagged_by: str = "the fact-check") -> dict:
        """The smallest fix for each flagged line, or a reason to keep it.

        Returns the `applied` rewrites (a row only where the text changed)
        and the lines `kept`; a failed call changes nothing and says so in
        `failed`. `rules` says the flags are the narration rules'
        (rule_findings), not the fact-check's: the prompt says so, shows
        what each shot puts on screen, and the fix may change that text.
        `back` names who sent the lines back (`yt script --revise`: the
        operator, retime's editor, QA through the feedback), and the prompt
        asks for the change each reason names instead of a fact fix, to the
        narration or, when the reason is about the screen, to the supers.
        Only the flagged numbers are answered: every other shot passed in is
        there to read them in. `flagged_by` names who flagged the lines
        otherwise: the fact-check asks for the smallest defensible change,
        the story review (`revise-story-N`) lets the line be restructured,
        and neither lets a fact be added.
        """
        by_n = {s.n: s for s in shots}
        todo: dict[int, list[dict]] = {}
        for f in flags:
            n = int(f.get("n", 0) or 0)
            if n in by_n and f.get("severity") in REVISABLE:
                todo.setdefault(n, []).append(f)
        result = {"applied": [], "kept": [], "failed": False}
        if not todo:
            return result
        self._say(f"[revise] {len(todo)} flagged line(s)")
        problems = "\n".join(
            f"{n}. " + " / ".join(f"{f['severity']}: {f.get('problem', '')} "
                                  f"(suggested: {f.get('fix', '')})" for f in fs)
            for n, fs in sorted(todo.items()))
        if rules:
            parts = [self._brief(), *([self.research] if self.research else []),
                     "The script as written:\n\n"
                     + shot_listing(shots, cite=True, supers=True),
                     "These lines break the narration rules, measured from the "
                     "text against the house's bounds:\n\n" + problems,
                     "For each flagged line make the smallest change that clears "
                     "the rule: to its narration, or to its on-screen text "
                     "(on_screen_text, every string it should show) when the rule "
                     "measured that. Keep every fact, the voice and the line's job "
                     "in the story. Never add a fact. A rule is a measured prior, "
                     "not a law: when the line is right as written, answer keep "
                     "and say why in one clause. Answer for every flagged number "
                     "and no other."]
        elif back:
            # Direction 29: after the read, rewrite the line that stumbles,
            # runs out of breath or buries its point. Judging 15: that pass
            # fixes the surface, so the line keeps its job in the story.
            # Every shot is listed, so a rewrite keeps the pronoun or the
            # connective that ties its line to the one before and after.
            parts = [self._brief(), *([self.research] if self.research else []),
                     "The screenplay as written, with what each shot puts on "
                     "screen:\n\n" + shot_listing(shots, cite=True, supers=True),
                     f"These lines were sent back to the screenplay by {back}, each "
                     "with the reason:\n\n" + problems,
                     "For each listed line make the change its reason asks for: to "
                     "its narration, or to its on-screen text (on_screen_text, every "
                     "string it should show) when the reason is about what the "
                     "screen shows. Rewrite a line that stumbles when said aloud, "
                     "runs out of breath before its end or buries its key term "
                     "behind a clause, and fit a line the read measured too long or "
                     "too short to its shot. Keep every fact the research supports, "
                     "the voice and the line's job in the story, read against the "
                     "lines around it. Never add a fact. When the line is right as "
                     "written, answer keep and say why in one clause. Answer for "
                     "every listed number and no other."]
        elif flagged_by != "the fact-check":
            parts = [self._brief(), *([self.research] if self.research else []),
                     "The script as written, with what each shot puts on screen:\n\n"
                     + shot_listing(shots, cite=True, supers=True),
                     f"These lines were flagged by {flagged_by}:\n\n" + problems,
                     "For each flagged line make the change its flag asks for to its "
                     "narration: you may restructure the line, never add a fact. Keep "
                     "the voice, every fact the research supports and the line's job "
                     "in the story, read against the lines around it, and keep it "
                     "near its shot's length. When the line is right as written, "
                     "answer keep and say why in one clause. Answer for every flagged "
                     "number and no other."]
        else:
            parts = [self._brief(), *([self.research] if self.research else []),
                     "The script as written:\n\n" + shot_listing(shots, cite=True),
                     f"These lines were flagged by {flagged_by}:\n\n" + problems,
                     "For each flagged line make the smallest change to its "
                     "narration that removes the problem: cut or soften the claim, "
                     "or state only what the research supports. Keep its length "
                     "within a couple of words, its voice, and every fact the "
                     "research does support. Never add a fact. When the line is "
                     "already defensible, answer keep and say why in one clause. "
                     "Answer for every flagged number and no other."]
        try:
            data = self._ask(name, "\n\n".join(parts),
                             RULE_REVISE_SCHEMA if rules
                             else BACK_REVISE_SCHEMA if back else REVISE_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[revise] failed ({exc}); keeping the lines as written")
            result["failed"] = True
            return result

        for fix in data.get("fixes", []):
            n = int(fix.get("n", 0) or 0)
            shot = by_n.get(n)
            if shot is None or n not in todo:
                continue
            why = str(fix.get("why", "")).strip()
            line = spoken(fix.get("narration", ""))
            if fix.get("action") == "keep":
                result["kept"].append({"n": n, "label": shot.label, "why": why})
                continue
            row = {"n": n, "label": shot.label, "why": why}
            if line and line != shot.narration:
                row |= {"before": shot.narration, "after": line}
                shot.narration, shot.revised = line, why
            # The screen is changed only where a rule measured it, or where
            # the line was sent back and the answer names new supers: an
            # optional `[]` beside a narration fix is the field left empty,
            # not an order to clear the shot's supers.
            given = fix.get("on_screen_text")
            text = ([" ".join(str(t).split()) for t in given if str(t).strip()]
                    if isinstance(given, list) else [])
            screen = (rules and isinstance(given, list)
                      and any(f.get("rule") in SCREEN_RULES for f in todo[n])) \
                or (bool(back) and bool(text))
            if screen:
                if text != list(shot.on_screen_text or []):
                    row |= {"on_screen_before": list(shot.on_screen_text or []),
                            "on_screen_after": text}
                    shot.on_screen_text = text
            if len(row) > 3:
                result["applied"].append(row)
        self._say(f"[revise] rewrote {len(result['applied'])}, "
                  f"kept {len(result['kept'])}")
        return result

    def verify_and_revise(self, shots: list[Shot], *, rounds: int = 2,
                          fix: bool = True, tag: str = "") -> dict:
        """Fact-check, fix the important and blocking lines, re-check just those.

        Stops when nothing binding is left or the rounds run out. The result
        is the check, with `revisions` (what changed), `kept` (lines left as
        written, with the reason, and still flagged) and `unresolved`. A
        `tag` names the decisions apart from the stage's own (`labels`:
        verify-labels, revise-labels-facts-1, verify-labels-1).
        """
        names = ((f"verify-{tag}", f"revise-{tag}-facts-{{}}", f"verify-{tag}-{{}}")
                 if tag else ("verify", "revise-{}", "verify-{}"))
        check = self.verify(shots, name=names[0])
        if not check or not fix:
            return check
        revisions: list[dict] = []
        kept: dict[int, str] = {}
        for round_no in range(1, max(0, rounds) + 1):
            todo = [f for f in check.get("flags", [])
                    if int(f.get("n", 0) or 0) not in kept]
            result = self.revise(shots, todo, name=names[1].format(round_no))
            kept.update({k["n"]: k["why"] for k in result["kept"]})
            revisions += result["applied"]
            changed = {r["n"] for r in result["applied"]}
            if not changed:
                break
            again = self.verify([s for s in shots if s.n in changed],
                                name=names[2].format(round_no))
            if not again:
                break
            check["flags"] = (
                [f for f in check.get("flags", []) if int(f.get("n", 0) or 0) not in changed]
                + [f for f in again.get("flags", []) if int(f.get("n", 0) or 0) in changed])
            check["recheck"] = again.get("verdict", "")
        for f in check.get("flags", []):
            if int(f.get("n", 0) or 0) in kept:
                f["kept"] = kept[int(f["n"])]
        check["revisions"] = revisions
        check["kept"] = [{"n": n, "why": why} for n, why in kept.items()]
        check["unresolved"] = [f for f in check.get("flags", [])
                               if f.get("severity") in REVISABLE and not f.get("kept")]
        return check

    # -- the story review: read before voice is paid for (AUTO-93) ---------

    def _story_prompt(self, sections: list[Section], plan: dict,
                      previous: dict | None = None) -> str:
        """What every sample of one round is shown: the brief, the plan, the
        script section by section with what each shot puts on screen, and in
        a revision round the review it revises. The rubric, with its levels
        in the sample's own order, is in each sample's role."""
        listing = "\n\n".join(
            f"Section {i}: {sec.heading}\n"
            + shot_listing(sec.shots, supers=True) for i, sec in enumerate(sections, 1))
        parts = [self._brief(), harness.brief("The plan", plan_view(plan)),
                 "The script, section by section; each shot is numbered first:\n\n"
                 + listing,
                 "Review this script as your role says. Tie every flag to its shot "
                 "number and its section number: a problem of one line is that "
                 "shot's; a problem of a whole section is shot 0 with that "
                 "section's number; a problem of the plan or the whole script is "
                 "shot 0 and section 0. A fix says what the writer changes, in the "
                 "script's own words; it may restructure a line or a section, "
                 "never ask for a fact the script does not have."]
        if previous:
            parts.append(
                f"The previous review of the draft this one revises (verdict "
                f"{previous.get('verdict')}; grades "
                + ", ".join(f"{k} {v}" for k, v in (previous.get("grades") or {}).items())
                + "). Its problems:\n"
                + "\n".join(f"- {story_flag_line(f)}" for f in previous.get("flags") or [])
                + "\n\nFill `previous`: whether this draft is better, the same or worse, "
                "and for each problem above whether it is resolved, persists or regressed.")
        return "\n\n".join(parts)

    @staticmethod
    def _sample_problems(review: dict, schema: dict) -> list[str]:
        """What one sample says that its rubric or its schema forbids: the
        rubric's own contradictions (narrative.check_review), a verdict or a
        grade off its list, and a revision round's missing `previous`."""
        if not isinstance(review, dict):
            return ["the answer is not a review"]
        found = narrative.check_review(review)
        if review.get("verdict") not in narrative.VERDICTS:
            found.append(f"the verdict {review.get('verdict')!r} is not one of "
                         f"{', '.join(narrative.VERDICTS)}")
        grades = review.get("grades") if isinstance(review.get("grades"), dict) else {}
        off = [k for k in narrative.KEYS if grades.get(k) not in narrative.LEVELS]
        if off:
            found.append(f"{', '.join(off)} carry no grade from "
                         f"{', '.join(narrative.LEVELS)}")
        if "previous" in schema.get("required", []) and \
                (review.get("previous") or {}).get("verdict") not in narrative.PREVIOUS:
            found.append("a revision round says whether this draft is better, the same "
                         "or worse than the one it revises: `previous` is missing")
        return found

    def story_sample(self, name: str, prompt: str, schema: dict) -> dict:
        """One sample of the story review, as a row. A sample whose answer
        breaks its rubric is asked once more as `<name>-again` with what it
        broke; still broken, it is not valid. A sample whose call fails is
        not valid either, and its failed row is in the engine's report: a
        failure is never read as a verdict."""
        row: dict = {"name": name, "valid": False, "again": False}
        try:
            review = self._ask(name, prompt, schema)
            problems = self._sample_problems(review, schema)
            if problems:
                self._say(f"[review] {name}: asking again ({'; '.join(problems)})")
                row["again"] = True
                review = self._ask(
                    f"{name}-again", prompt + "\n\nYour previous answer broke the "
                    "rubric it was graded on:\n" + "\n".join(f"- {p}" for p in problems)
                    + "\nGrade again, keeping your verdict, grades and flags consistent.",
                    schema)
                problems = self._sample_problems(review, schema)
        except ClaudeError as exc:
            self._say(f"[review] {name} failed ({exc})")
            row["failed"] = str(exc)
            return row
        grades = review.get("grades") if isinstance(review.get("grades"), dict) else {}
        row.update(verdict=review.get("verdict"), grades=dict(grades),
                   flags=[f for f in review.get("flags") or [] if isinstance(f, dict)],
                   logline=review.get("logline", ""), synopsis=review.get("synopsis", ""),
                   first_minute=review.get("first_minute") or {})
        if isinstance(review.get("previous"), dict):
            row["previous"] = review["previous"].get("verdict")
            row["findings"] = review["previous"].get("findings") or []
        if problems:
            row["unsettled"] = problems
        else:
            row["valid"] = True
        return row

    def review_round(self, sections: list[Section], plan: dict, round_no: int,
                     previous: dict | None = None) -> dict:
        """One round of the story review: `self.samples` samples on the same
        prompt, `review-r<round>-s1..`, each with its own level order. The
        grade per criterion and the verdict are the medians of the valid
        samples (narrative.median); fewer than two valid samples is
        `not_evidenced`, never a verdict."""
        prompt = self._story_prompt(sections, plan, previous)

        def one(k: int) -> dict:
            schema = narrative.review_schema(delta=previous is not None, seed=k)
            return self.story_sample(f"review-r{round_no}-s{k}", prompt, schema)
        seeds = range(1, self.samples + 1)
        if self.jobs > 1 and self.samples > 1:
            # The samples are independent readings; the books are shared
            # under the harness's lock, and the rows keep the seeds' order.
            with ThreadPoolExecutor(max_workers=min(self.jobs, self.samples)) as pool:
                samples = list(pool.map(one, seeds))
        else:
            samples = [one(k) for k in seeds]
        valid = [s for s in samples if s["valid"]]
        grades = {k: narrative.median([s["grades"].get(k) for s in valid], narrative.LEVELS)
                  for k in narrative.KEYS} if valid else {}
        verdict = (narrative.median([s["verdict"] for s in valid], narrative.VERDICTS,
                                    borderline="consider")
                   if len(valid) >= 2 else "not_evidenced")
        out = {"round": round_no, "grades": grades, "verdict": verdict,
               "samples": samples, "flags": merge_flags(valid)}
        if previous is not None:
            out["previous"] = narrative.median([s.get("previous") for s in valid],
                                               narrative.PREVIOUS, borderline="same") \
                if len(valid) >= 2 else None
        self._say(f"[review] round {round_no}: {verdict} "
                  f"({len(valid)} of {len(samples)} sample(s) valid)")
        self._log_verdict(out)
        return out

    def _log_verdict(self, rnd: dict) -> None:
        """Append the round's verdict to the review log, for the calibration
        set (judging 10). A round served whole from the cache is the same
        verdict again and is not appended twice."""
        if self.review_log is None:
            return
        names = {s["name"] for s in rnd["samples"]}
        replayed = all(any(d.name.startswith(n) and d.cached for d in self.harness.decisions)
                       for n in names)
        if replayed:
            return
        row = {"slug": Path(self.root).name if self.root else "",
               "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "round": rnd["round"],
               "grades": rnd["grades"], "verdict": rnd["verdict"],
               "samples": [{k: s.get(k) for k in ("name", "verdict", "grades", "valid",
                                                  "again", "failed")}
                           for s in rnd["samples"]],
               "model": self.reviewer_model or self.model, "label": None}
        try:
            self.review_log.parent.mkdir(parents=True, exist_ok=True)
            with self.review_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.findings.append(f"warning: story review: the verdict could not be "
                                 f"appended to {self.review_log} ({exc})")

    def cold_read(self, shots: list[Shot], plan: dict) -> dict:
        """Judging 14: a reader who sees the narration and nothing else (no
        research, no plan, no house) restates the premise, names the terms
        it was never told, says why each major claim should matter, and
        where each of the plan's questions was answered, listed without
        where the plan meant to answer them (`cold-read`). Its premise is
        then matched against the train (`cold-read-match`). A failed call
        is recorded; the review's samples still stand."""
        questions = [q for q in plan.get("questions") or [] if isinstance(q, dict)]
        parts = ["The narration, shot by shot, each with its number:\n\n"
                 + "\n".join(f"{s.n}. {s.narration or SILENT}" for s in shots)]
        if questions:
            parts.append("Questions a viewer may be left asking. For each one this "
                         "narration answers, give its id and the number of the shot "
                         "that answers it; leave out any it never answers:\n"
                         + "\n".join(f"- {q.get('id')}: {q.get('text')}" for q in questions))
        parts.append("Restate the film's premise in your own words, list the terms a "
                     "viewer needs and is never told, and for each major claim say why "
                     "a viewer should care about it as the narration puts it.")
        try:
            read = self._ask("cold-read", "\n\n".join(parts), COLD_READ_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[review] cold-read failed ({exc})")
            return {"failed": str(exc)}
        answered = [a for a in read.get("answered") or [] if isinstance(a, dict)]
        heard = {str(a.get("question_id")) for a in answered}
        out = {"premise": str(read.get("premise", "")),
               "unexplained_terms": list(read.get("unexplained_terms") or []),
               "claims": list(read.get("claims") or []), "answered": answered,
               "unanswered": [str(q.get("id")) for q in questions
                              if str(q.get("id")) not in heard],
               "match": "", "difference": ""}
        train = str(plan.get("train") or "").strip()
        if not train or not out["premise"]:
            return out
        try:
            match = self._ask(
                "cold-read-match",
                f"The train the film was planned on:\n{train}\n\nWhat a first-time viewer "
                f"took the film to be, from its narration alone:\n{out['premise']}\n\n"
                "Do they name the same question and the same obstacle?", MATCH_SCHEMA)
        except ClaudeError as exc:
            self._say(f"[review] cold-read-match failed ({exc})")
            out["match_failed"] = str(exc)
            return out
        out["match"] = str(match.get("verdict", ""))
        out["difference"] = str(match.get("difference", ""))
        return out

    def _renumber(self, sections: list[Section]) -> list[Shot]:
        """Number every shot again after a section was redrafted: its place
        in the film, its label within its section and where it starts."""
        shots, at = [], 0
        for index, section in enumerate(sections, 1):
            for i, shot in enumerate(section.shots, 1):
                shot.n, shot.label = len(shots) + 1, f"{index}.{i}"
                shot.at, at = at, at + shot.seconds
                shots.append(shot)
        return shots

    def _redraft(self, sections: list[Section], specs: list[dict], plan: dict,
                 index: int, flags: list[dict], name: str) -> dict | None:
        """Write section `index` again with the review's flags on it, as
        `name` (`redraft-NN`). The section keeps its old shots when the
        call fails or comes back empty."""
        before = [s for sec in sections[:index - 1] for s in sec.shots]
        after = sections[index].shots if index < len(sections) else []
        old = sections[index - 1].shots
        try:
            shots = self.write_section(
                specs[index - 1], index=index, total=len(specs), plan=plan,
                written=before, sourced=bool(self.research),
                previous=specs[index - 2] if index > 1 else None,
                ledger=Ledger.from_plan(specs),
                next_heading=str(specs[index].get("heading", "")) if index < len(specs) else "",
                name=name, redraft=[story_flag_line(f) for f in flags], after=after)
        except ClaudeError as exc:
            self._say(f"[review] {name} failed ({exc}); the section stays as written")
            return None
        if not shots:
            return None
        sections[index - 1].shots = shots
        return {"section": index, "name": name, "shots_before": len(old),
                "shots_after": len(shots),
                "flags": [story_flag_line(f) for f in flags]}

    def review_and_revise(self, sections: list[Section], plan: dict, *,
                          fix: bool = True) -> dict:
        """Read the drafted script before voice is paid for, and send back
        what a consider names.

        Round 1 is `self.samples` samples (`review-r1-s1..`) and the cold
        read. On a consider (and with `fix`), each binding flag goes where
        it can be fixed: a shot's to `revise-story-<round>`, a section's to
        `redraft-NN`, and one on the plan or the whole script (shot 0,
        section 0) is left unresolved with REPLAN_FIX, since a revision of
        lines cannot reach it. The revised script is read again with the
        review it revises (`review-r<round+1>-s..`, `previous` required);
        it stands only when the samples call it better, else it is put back
        and the rounds stop. Nothing here edits a line: every change is the
        writer's answer (invariant 3). A recommend, a reject and
        not_evidenced are recorded as they are."""
        specs = plan.get("sections") or []
        shots = [s for sec in sections for s in sec.shots]
        current = self.review_round(sections, plan, 1)
        cold = self.cold_read(shots, plan)
        record: dict = {"model": self.reviewer_model or self.model, "rounds": [],
                        "revisions": [], "redrafts": [], "kept": [], "unresolved": [],
                        "cold_read": cold, "previous_verdicts": [], "reverted": False}
        plan_flags: list[dict] = []
        if cold.get("match") == "mismatch":
            plan_flags.append({
                "n": 0, "section": 0, "criterion": "train", "severity": "important",
                "problem": f"a first-time reader took the film to be: {cold['premise']}"
                           + (f" ({cold['difference']})" if cold.get("difference") else ""),
                "fix": "make the train's question and obstacle heard in the narration",
                "by": "cold-read-match"})
        record["rounds"].append({k: current[k] for k in ("round", "verdict", "grades")})
        round_no = 1
        while current["verdict"] == "consider" and fix and round_no <= self.rounds:
            binding = [f for f in current["flags"] if f.get("severity") in REVISABLE]
            by_n = {s.n: s for s in shots}
            to_shots, to_sections = [], {}
            for f in binding:
                n, section = int(f.get("n", 0) or 0), int(f.get("section", 0) or 0)
                if n in by_n:
                    to_shots.append(f)
                elif 1 <= section <= len(sections) and section <= len(specs):
                    to_sections.setdefault(section, []).append(f)
                else:
                    plan_flags.append(f)
            # A section written again takes its shots' flags with it.
            to_shots = [f for f in to_shots
                        if by_n[int(f["n"])].label.split(".")[0]
                        not in {str(i) for i in to_sections}]
            if not to_shots and not to_sections:
                break
            kept_shots = copy.deepcopy([sec.shots for sec in sections])
            result = self.revise(shots, to_shots, name=f"revise-story-{round_no}",
                                 flagged_by="the story review")
            redrafts = []
            for index, flags_on in sorted(to_sections.items()):
                done = self._redraft(sections, specs, plan, index, flags_on,
                                     f"redraft-{index:02d}" if round_no == 1
                                     else f"redraft-{index:02d}-r{round_no}")
                if done:
                    redrafts.append(done)
            shots = self._renumber(sections)
            record["kept"] += result["kept"]
            if not result["applied"] and not redrafts:
                break
            again = self.review_round(sections, plan, round_no + 1, previous=current)
            judged = again.get("previous") or "not_evidenced"
            record["previous_verdicts"].append(judged)
            record["rounds"].append({"round": round_no + 1, "verdict": again["verdict"],
                                     "grades": again["grades"], "previous": judged,
                                     "revisions": result["applied"], "redrafts": redrafts})
            if again["verdict"] == "not_evidenced" or judged != "better":
                # The revision is not shown to be better: the draft it
                # revised stands, with its review.
                for sec, old in zip(sections, kept_shots):
                    sec.shots = old
                shots = self._renumber(sections)
                record["reverted"] = True
                record["rounds"][-1]["reverted"] = True
                break
            record["revisions"] += result["applied"]
            record["redrafts"] += redrafts
            current = again
            round_no += 1
        if current["verdict"] in ("consider", "reject"):
            plan_flags += [f for f in current["flags"] if f.get("severity") in REVISABLE
                           and not int(f.get("n", 0) or 0) and not int(f.get("section", 0) or 0)]
        seen = set()
        for f in plan_flags:
            key = (f.get("criterion"), str(f.get("problem")))
            if key not in seen:
                seen.add(key)
                record["unresolved"].append(dict(f, command=REPLAN_FIX))
        record.update(grades=current["grades"], verdict=current["verdict"],
                      samples=current["samples"], flags=current["flags"])
        return record

    def story_findings(self, record: dict) -> list[str]:
        """The story review in the stage's findings: a reject is an `error:`
        (doctor fails on it; it stops voice only under --gate), a review
        with too few valid samples a `warning:`, and each flag no revision
        can reach a `warning:` with the command that can."""
        out = []
        verdict = record.get("verdict")
        grades = record.get("grades") or {}
        weak = ", ".join(f"{k} {v}" for k, v in grades.items() if v in ("Poor", "Fair"))
        if verdict == "reject":
            out.append(f"error: story review: reject ({weak or 'see script.md'}); "
                       f"`{REPLAN_FIX}` with --notes saying what the review asks")
        elif verdict == "not_evidenced":
            valid = sum(1 for s in record.get("samples") or [] if s.get("valid"))
            out.append(f"warning: story review: not_evidenced ({valid} of "
                       f"{len(record.get('samples') or [])} sample(s) valid); the script "
                       "was not judged")
        elif verdict == "consider":
            out.append(f"note: story review: consider ({weak or 'no weak grade'}); its "
                       "flags are in script.md")
        for f in record.get("unresolved") or []:
            out.append(f"warning: story review: {story_flag_line(f)}; a revision cannot "
                       f"reach it: `{f.get('command', REPLAN_FIX)}`")
        cold = record.get("cold_read") or {}
        if cold.get("failed"):
            out.append(f"warning: story review: the cold read failed ({cold['failed']})")
        return out

    def _drop_repeats(self, sections: list[Section], shots: list[Shot]) -> int:
        """Reword the later mention of a figure two sections both said."""
        rows = repeated_figures(sections)
        if not rows:
            return 0
        flags = [{"n": r["n"], "severity": "important",
                  "problem": f"states {r['figure']}, already said at {r['first']}; "
                             "a figure is announced once",
                  "fix": "refer back without the figure, or cut it"} for r in rows]
        self._say(f"[ledger] {len(rows)} repeated figure(s)")
        return len(self.revise(shots, flags, name="revise-ledger")["applied"])

    def revise_rules(self, sections: list[Section], shots: list[Shot], plan: dict,
                     *, rounds: int = 2) -> dict:
        """Measure the narration against RULES and send the error and
        important flags to revise (`revise-rules`, then `revise-rules-2`):
        fix the line or keep it with a reason. The rules are read again
        after every change; a line revised and still breaking its rule goes
        round once more. Returns the `revisions` and what was `kept`, keyed
        by (rule, shot number). The house and minor tiers are not sent."""
        context = {**plan, "idea": asdict(self.idea)}
        revisions: list[dict] = []
        kept: dict[tuple, str] = {}
        for round_no in range(1, max(0, rounds) + 1):
            flags = rule_findings(context, sections, self.wpm, self.lang)
            todo = [f for f in flags if f["severity"] in REVISABLE
                    and (f["rule"], f["n"]) not in kept]
            if not todo:
                break
            self._say(f"[rules] {len(todo)} rule flag(s) to revise")
            result = self.revise(shots, todo, rules=True, name="revise-rules"
                                 + (f"-{round_no}" if round_no > 1 else ""))
            for k in result["kept"]:
                kept.update({(f["rule"], f["n"]): k["why"] for f in todo
                             if f["n"] == k["n"]})
            revisions += result["applied"]
            if not result["applied"]:
                break
        return {"revisions": revisions, "kept": kept}

    # -- run ---------------------------------------------------------------

    def _draft(self, index: int, specs: list[dict], plan: dict, sourced: bool,
               ledger: Ledger, written: list[Shot]) -> tuple[Section, list[Shot], bool]:
        spec = specs[index - 1]
        section = Section.from_spec(spec, heading=f"Section {index}")
        try:
            shots = self.write_section(
                spec, index=index, total=len(specs), plan=plan,
                written=written, sourced=sourced,
                previous=specs[index - 2] if index > 1 else None,
                ledger=ledger,
                next_heading=str(specs[index].get("heading", ""))
                if index < len(specs) else "")
            failed = False
        except ClaudeError as exc:
            # One failed section must not cost the whole screenplay.
            self._say(f"  section {index} failed: {exc}")
            shots, failed = [], True
        return section, shots, failed

    def run(self, *, do_polish: bool = False, do_verify: bool = False,
            fix: bool = True, review: bool = False) -> dict:
        plan = self.plan()
        specs = plan.get("sections") or []
        if not specs:
            raise ClaudeError("the plan came back with no sections")
        # The plan is read before any section is paid for (AUTO-92).
        plan_review = None
        if review:
            plan, plan_review = self.gate_plan(plan, fix=fix)
            specs = plan.get("sections") or []
            if not specs:
                raise ClaudeError("the plan came back with no sections")

        sourced = bool(self.research)
        ledger = Ledger.from_plan(specs)
        sections: list[Section] = []
        written: list[Shot] = []
        failures = 0

        def number(index: int, section: Section, shots: list[Shot]) -> None:
            for i, shot in enumerate(shots, 1):
                shot.n = len(written) + i
                shot.label = f"{index}.{i}"
            section.shots = shots
            written.extend(shots)
            sections.append(section)

        if self.jobs > 1 and len(specs) > 1:
            self._say(f"[write] {len(specs)} sections, {min(self.jobs, len(specs))} "
                      "at a time")
            with ThreadPoolExecutor(max_workers=min(self.jobs, len(specs))) as pool:
                drafts = list(pool.map(
                    lambda i: self._draft(i, specs, plan, sourced, ledger, []),
                    range(1, len(specs) + 1)))
            for index, (section, shots, failed) in enumerate(drafts, 1):
                failures += failed
                number(index, section, shots)
        else:
            for index in range(1, len(specs) + 1):
                section, shots, failed = self._draft(index, specs, plan, sourced,
                                                     ledger, written)
                failures += failed
                number(index, section, shots)

        at = 0
        for shot in written:
            shot.at, at = at, at + shot.seconds
            if shot.function not in FUNCTIONS:
                # Kept as the writer wrote it; qa, pilot and build act only on
                # the listed roles, so the shot is read as body.
                self.findings.append(f"note: shot {shot.label}: function "
                                     f"{shot.function!r} not in the schema; "
                                     "treated as body")

        if written:
            self._drop_repeats(sections, written)
        polished = self.polish(written) if do_polish and written else 0
        # The story is read after the polish and before the rules and the
        # fact-check (AUTO-93): a line it restructures is then measured and
        # checked like any other, and a section it redrafts is numbered again.
        story = (self.review_and_revise(sections, plan, fix=fix)
                 if review and written else None)
        if story is not None:
            written[:] = [s for sec in sections for s in sec.shots]
            self.findings += self.story_findings(story)
        # The narration rules after the polish and before the fact-check, so
        # the check reads the lines the rules changed (AUTO-94).
        ruled = (self.revise_rules(sections, written, plan)
                 if fix and written else {"revisions": [], "kept": {}})
        check = (self.verify_and_revise(written, rounds=self.rounds, fix=fix)
                 if do_verify and written else {})
        # Read once more on the final text: the fact-check's rewrites count.
        flags = (rule_findings({**plan, "idea": asdict(self.idea)}, sections,
                               self.wpm, self.lang) if written else [])
        kept = ruled["kept"]
        self.findings += rule_lines(flags, kept, self.lang, revised=fix)

        return {
            "idea": asdict(self.idea),
            "logline": plan.get("logline", ""),
            "promise": plan.get("promise", ""),
            "payoff": plan.get("payoff", ""),
            # The story the plan locked (AUTO-90), as the model answered it.
            **{k: plan[k] for k in STORY_PROPERTIES
               if k in plan and k not in ("promise", "payoff")},
            "findings": list(self.findings),
            "target_seconds": self.seconds,
            "wpm": self.wpm,
            "sections": [asdict(s) for s in sections],
            "polished": polished,
            "failed_sections": failures,
            "lang": self.lang,
            "house": self.house_digest,
            # The reviewer's reading of the plan, when it was asked (AUTO-92).
            **({"plan_review": plan_review} if plan_review else {}),
            # The story review's samples, verdict and revisions (AUTO-93).
            **({"review": story} if story is not None else {}),
            "check": check,
            # The rules' flags still standing on the final text, what their
            # revision changed and what it kept, with the reason.
            "rules": {"flags": flags, "revisions": ruled["revisions"],
                      "kept": [{"rule": r, "n": n, "why": why}
                               for (r, n), why in kept.items()]},
            "usage": spend.from_usage(self.usage, self.model),
            "engine": self.harness.report(),
        }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

#: The delivery gate reads a caption at 17 characters a second; a super on
#: screen is read the same way, so the same limit holds for it. This is the
#: measurable half of the house rule about reserving time for reading.
READING_CPS = 17.0

#: The fact-check severities the stage fixes itself.
REVISABLE = ("blocker", "important")

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


#: Strings quoted inside a direction: straight, curly or guillemets.
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|«\s*([^»]+?)\s*»')


def visible_text(shot: Shot) -> str:
    """The words a viewer reads from a shot, without the directions around them.

    The screenplay keeps them in `on_screen_text`. An older screenplay has only
    `on_screen`: the strings quoted in it are the words, and with no quotes the
    whole field is taken, as before.
    """
    strings = [t for t in shot.on_screen_text or [] if str(t).strip()]
    if not strings:
        field_text = shot.on_screen or ""
        strings = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(field_text)]
        if not strings:
            strings = [field_text]
    return " ".join(" ".join(strings).split())


def legibility(sections: list[Section], timings: dict | None = None) -> list[dict]:
    """Supers nobody can finish reading in the time they are on screen."""
    out = []
    for section in sections:
        for shot in section.shots:
            text = visible_text(shot)
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
            shown = visible_text(shot)
            for raw in re.findall(r"\d[\d.,]*", shown):
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
                            "super": shown[:50]})
    return out


# --------------------------------------------------------------------------- #
# the narration rules (AUTO-94)
# --------------------------------------------------------------------------- #
#
# What docs/narrative lets a lint measure in the text, measured before voice
# is paid for. A rule finds; it never rewrites: the error and important tiers
# go to `revise` (decision `revise-rules`), which fixes the line or keeps it
# with a reason, and what still stands is recorded in script.json's
# `findings` with sdkkit's prefixes. Judgements the corpus asks for that a
# word list cannot make (an abstract opener, a first line that is concrete,
# a callback, a return to scale, a concrete title) are not here: they belong
# to a reviewer's rubric, not a lint.

#: How hard a rule binds. `error`: revised, and a finding still standing
#: blocks the stage. `important`: revised, and one still standing is a
#: warning. `house`: recorded as a warning only, never sent to revise.
#: `minor`: a warning, not revised.
TIERS = ("error", "important", "house", "minor")

#: A tier as a CHECK_SCHEMA severity, so `revise` reads a rule flag as it
#: reads the fact-check's: only blocker and important are REVISABLE.
_SEVERITY = {"error": "blocker", "important": "important", "house": "minor",
             "minor": "minor"}

#: Every threshold, with the corpus rule it comes from and the strength the
#: corpus README's table gives that rule, or `house` where the number is this
#: pipeline's own (a house number inside a stronger rule is still house).
#: They are priors until the evidence loop (`yt improve` fed by the channel's
#: retention) replaces them. No number the corpus calls folklore is here:
#: not the seven-word anecdote, not 160 wpm, not the 3/5/8/15 s hook windows
#: the vendors quote; the hook window is YouTube's own 30 s Intro.
BOUNDS: dict[str, dict] = {
    "filler": {
        "value": "before the first content sentence", "corpus": ("structure 11",),
        "strength": "platform",
        "note": "welcome, subscribe, my name is, in this video I; the channel's "
                "name is not known to the stage, so it is not in the list. A "
                "sentence that also says a word of the title is the first "
                "content sentence (an in-character greeting that sets up the "
                "show), not filler"},
    "sentence": {
        "value": 25, "unit": "words in one sentence", "corpus": ("direction 16",),
        "strength": "practitioner",
        "note": "the 25-word ceiling is a training manual's; the 15-20 target "
                "is from an uncredited guide and is not enforced"},
    "unspoken_number": {
        "value": "a figure on screen the narration never says, in digits or "
                 "common number words", "corpus": ("direction 1", "direction 2"),
        "strength": "house",
        "note": "a heuristic: a figure may be said in words the list lacks"},
    "legibility": {
        "value": READING_CPS, "unit": "characters a second of on-screen text",
        "corpus": ("direction 3",), "strength": "platform"},
    "numbers": {
        "value": 2, "unit": "numerals in one sentence", "corpus": ("direction 21",),
        "strength": "practitioner"},
    "hook_window": {
        "value": 30, "unit": "seconds from the top: the title's subject is "
                            "said or shown", "corpus": ("structure 9", "structure 10",
                                                        "structure 13"),
        "strength": "platform",
        "note": "YouTube's Intro measure; no vendor window is used. Any "
                "content word of the title counts, its verb included: telling "
                "the key noun from the verb needs a parser the stage does not "
                "have, so a hook that says only 'it fell' passes and is left "
                "to the judged rubric"},
    "hook_answer": {
        "value": 4, "unit": "words, the shortest payoff sentence matched "
                            "verbatim in the 30 s window", "corpus": ("structure 13",),
        "strength": "house"},
    "payoff_last": {
        "value": 0.5, "unit": "share of the payoff line's content words in the "
                              "last section", "corpus": ("structure 7", "structure 33"),
        "strength": "house"},
    "clause": {
        "value": 2, "unit": "clauses in one sentence (relative which/that/who, "
                            "or a comma before and/but/so)", "corpus": ("direction 17",),
        "strength": "house",
        "note": "subject-verb distance needs a parser and is not measured"},
    "passive": {
        "value": 0.10, "unit": "share of sentences in the passive",
        "corpus": ("direction 19",), "strength": "house",
        "note": "be plus a past participle; English only"},
    "person": {
        "value": 0, "unit": "second-person or first-plural words in a section",
        "corpus": ("attention 25",), "strength": "house",
        "note": "the corpus gives no threshold; a section that never addresses "
                "the viewer is the floor"},
    "pace": {
        "value": 1.10, "unit": "the section's words read at --wpm, over its "
                               "narrated shots' planned seconds",
        "corpus": ("direction 25",), "strength": "house",
        "note": "consistency with --wpm, the pace the film is planned at: do "
                "the words fit the shots at that read? It is not direction "
                "25's band (170-190 wpm recalled best, warn outside 150-190); "
                "voice and retime measure the read"},
    "density": {
        "value": 8, "unit": "numerals and new proper nouns per 100 words of a "
                            "section", "corpus": ("direction 26",), "strength": "house"},
    "hook_promise": {
        "value": 1 / 3, "unit": "share of the promise's content words in the 30 s "
                                "window", "corpus": ("structure 10",), "strength": "house"},
    "hook_stakes": {
        "value": 90, "unit": "seconds from the top: a line addressed to the viewer",
        "corpus": ("attention 10",), "strength": "house"},
    "end_signal": {
        "value": 0.20, "unit": "the last share of the runtime searched for end "
                               "signals", "corpus": ("structure 28",),
        "strength": "practitioner",
        "note": "'finally' and 'before we go' are house; 'finally' in an "
                "enumeration is exempt"},
    "recap": {
        "value": 25, "unit": "words of narration after the payoff shot",
        "corpus": ("structure 27",), "strength": "practitioner"},
    "last_line": {
        "value": 20, "unit": "words in the film's last narrated sentence",
        "corpus": ("structure 33",), "strength": "practitioner"},
}


@dataclass(frozen=True)
class Rule:
    """One measurable rule: its id, tier, the languages its lexicon or syntax
    covers (empty: it measures every language) and its key in BOUNDS."""
    id: str
    tier: str
    langs: tuple[str, ...]
    bound: str

    @property
    def corpus(self) -> tuple[str, ...]:
        return BOUNDS[self.bound]["corpus"]


_LEXICAL = ("en", "pt")

RULES: tuple[Rule, ...] = (
    Rule("rule:filler", "error", _LEXICAL, "filler"),
    Rule("rule:sentence", "error", (), "sentence"),
    Rule("rule:unspoken-number", "important", _LEXICAL, "unspoken_number"),
    Rule("rule:legibility", "important", (), "legibility"),
    Rule("rule:numbers", "important", (), "numbers"),
    Rule("rule:hook-subject", "important", _LEXICAL, "hook_window"),
    Rule("rule:hook-answer", "important", (), "hook_answer"),
    Rule("rule:payoff-last", "important", _LEXICAL, "payoff_last"),
    Rule("rule:clause", "house", _LEXICAL, "clause"),
    Rule("rule:passive", "house", ("en",), "passive"),
    Rule("rule:person", "house", _LEXICAL, "person"),
    Rule("rule:pace", "house", (), "pace"),
    Rule("rule:density", "house", _LEXICAL, "density"),
    Rule("rule:hook-promise", "house", _LEXICAL, "hook_promise"),
    Rule("rule:hook-stakes", "house", _LEXICAL, "hook_stakes"),
    Rule("rule:end-signal", "house", _LEXICAL, "end_signal"),
    Rule("rule:recap", "house", _LEXICAL, "recap"),
    Rule("rule:last-line", "minor", (), "last_line"),
)
_RULE = {r.id: r for r in RULES}

#: The rules that measure what the shot shows rather than what it says: only
#: a flag of one of these lets `revise` change the shot's on_screen_text.
SCREEN_RULES = ("rule:legibility", "rule:unspoken-number")

#: The words a lexical rule looks for, by language. Lower case; a phrase
#: matches on word boundaries.
LEXICON = {
    "en": {
        "filler": ("welcome", "subscribe", "my name is", "in this video i"),
        "stop": ("a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to",
                 "for", "from", "by", "with", "about", "into", "over", "up", "down",
                 "out", "off", "how", "why", "what", "when", "where", "who", "which",
                 "that", "this", "these", "those", "is", "are", "was", "were", "be",
                 "been", "it", "its", "you", "your", "we", "our", "they", "their",
                 "he", "she", "his", "her", "will", "can", "did", "does", "not",
                 "one", "nobody", "all", "just", "very"),
        "relative": ("which", "that", "who", "whom", "whose"),
        "joins": ("and", "but", "so"),
        "viewer": ("you", "your", "yours", "yourself", "yourselves"),
        "person": ("you", "your", "yours", "yourself", "we", "us", "our", "ours",
                   "let's"),
        "end": ("in summary", "to sum up", "in conclusion", "that's about it",
                "that's all", "wrap things up", "to wrap up", "before we go", "finally"),
        "finally": ("finally",),
        "first": ("first",),
        "sign_off": ("thanks for watching", "thank you for watching",
                     "that's it for today", "in this video we saw",
                     "see you next time"),
    },
    "pt": {
        "filler": ("bem-vindo", "bem-vinda", "bem-vindos", "bem-vindas", "inscreva",
                   "inscrevam", "meu nome é", "neste vídeo eu", "nesse vídeo eu"),
        "stop": ("o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da",
                 "dos", "das", "em", "no", "na", "nos", "nas", "por", "pelo", "pela",
                 "para", "pra", "com", "sem", "como", "que", "qual", "quem", "porque",
                 "quando", "onde", "e", "ou", "mas", "é", "são", "foi", "era", "ser",
                 "se", "seu", "sua", "você", "vocês", "nós", "eles", "elas", "ele",
                 "ela", "isso", "isto", "esse", "essa", "este", "esta", "não", "ninguém"),
        "relative": ("que", "qual", "quais", "quem", "cujo", "cuja", "cujos", "cujas"),
        "joins": ("e", "mas"),
        "viewer": ("você", "vocês", "te", "ti", "contigo", "teu", "tua"),
        "person": ("você", "vocês", "nós", "nosso", "nossa", "nossos", "nossas",
                   "vamos", "a gente"),
        "end": ("em resumo", "resumindo", "em conclusão", "para encerrar",
                "pra encerrar", "para finalizar", "pra finalizar", "antes de irmos",
                "finalmente", "por fim"),
        "finally": ("finalmente", "por fim"),
        "first": ("primeiro", "primeira"),
        "sign_off": ("obrigado por assistir", "obrigada por assistir",
                     "por hoje é só", "neste vídeo vimos", "nesse vídeo vimos",
                     "até a próxima"),
    },
}

#: be plus a past participle (a regular -ed, or a common irregular one).
_PASSIVE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?"
    r"(\w{2,}ed|built|made|done|known|seen|found|held|kept|left|lost|paid|sold|"
    r"told|brought|caught|taught|bought|sent|shown|given|taken|written|driven|"
    r"broken|chosen|frozen|spoken|stolen|hidden|beaten|eaten|forgotten|born)\b", re.I)
_NUMERAL = re.compile(r"\d+(?:[.,]\d+)*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_CAPITALISED = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w'’-]*$")


def rule_lang(lang: str) -> str:
    """The language a rule is read in: the primary subtag, English when the
    film names none (the stage writes English unless --lang says otherwise)."""
    return (str(lang or "").strip().lower().replace("_", "-").split("-")[0]) or "en"


def _applies(rule: Rule, lang: str) -> bool:
    return not rule.langs or rule_lang(lang) in rule.langs


def not_evidenced(lang: str) -> list[str]:
    """One line for each rule the film's language takes out of reach: the
    rule is not run, and the record says so rather than staying silent."""
    shown = str(lang or "").strip() or "en"
    return [f"not_evidenced: {r.id} ({shown})" for r in RULES if not _applies(r, lang)]


def sentences(text: str) -> list[str]:
    """A narration line split at sentence ends (. ! ? …) followed by a space."""
    return [s for s in _SENTENCE_END.split(" ".join(str(text or "").split())) if s]


def _words(text: str) -> list[str]:
    return re.findall(r"[\w'’-]+", str(text or "").lower())


def _has(text: str, phrases) -> list[str]:
    """The phrases of `phrases` found in `text`, on word boundaries."""
    low = " ".join(str(text or "").lower().replace("’", "'").split())
    return [p for p in phrases
            if re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w])", low)]


def _content(text: str, lex: dict) -> list[str]:
    """The words that carry the meaning: not a stop word, three letters or
    more, or any token with a digit. In order, once each."""
    out: list[str] = []
    for w in _words(text):
        w = w.strip("'’-")
        if w and w not in lex["stop"] and (len(w) >= 3 or any(c.isdigit() for c in w)) \
                and w not in out:
            out.append(w)
    return out


def _stem(word: str) -> str:
    """Enough of a word to match its plural or tense: bridge / bridges."""
    return word[:5] if len(word) > 5 else word


def _found(words: list[str], text: str) -> list[str]:
    stems = {_stem(w) for w in _words(text)}
    return [w for w in words if _stem(w) in stems]


def _clock(sections: list[Section]) -> list[tuple[Section, Shot, float]]:
    """Every shot with its start, from the shots' own lengths, in order."""
    out, at = [], 0.0
    for section in sections:
        for shot in section.shots:
            out.append((section, shot, at))
            at += float(shot.seconds or 0)
    return out


def _flag(rule: str, shot: Shot | None, problem: str, fix: str) -> dict:
    tier = _RULE[rule].tier
    where = f"shot {shot.label}: " if shot is not None and shot.label else ""
    return {"n": shot.n if shot is not None else 0, "rule": rule, "tier": tier,
            "severity": _SEVERITY[tier], "problem": f"{rule}: {where}{problem}",
            "fix": fix}


def rule_findings(plan: dict, sections: list[Section], wpm: int = DEFAULT_WPM,
                  lang: str = "") -> list[dict]:
    """The narration measured against RULES, as flags in CHECK_SCHEMA's
    shape: `n`, `severity`, `problem` (opening with the rule), `fix`, plus
    `rule` and `tier`. `plan` is the plan or script.json (its title under
    `idea`, its `promise`, `payoff` and `ending`); a field it lacks takes its
    rule out quietly, as an older screenplay has none. A rule whose
    languages exclude `lang` is not run here; `not_evidenced` names it."""
    plan = plan or {}
    on = {r.id: _applies(r, lang) for r in RULES}
    lex = LEXICON.get(rule_lang(lang), LEXICON["en"])
    timed = _clock(sections)
    runtime = sum(float(shot.seconds or 0) for _, shot, _ in timed)
    voiced = [(sec, shot, at) for sec, shot, at in timed if spoken(shot.narration)]
    out: list[dict] = []

    title = str(plan.get("title") or (plan.get("idea") or {}).get("title") or "")
    subject = _content(title, lex)

    # -- line by line: filler, sentence, numbers, clause --------------------
    # A greeting that names the title's subject sets the show up (structure
    # 11 does not count it against the opening): it is the first content
    # sentence, not filler before it.
    content_started = False
    for _, shot, _ in voiced:
        for sentence in sentences(spoken(shot.narration)):
            words = sentence.split()
            if not content_started and on["rule:filler"]:
                hits = _has(sentence, lex["filler"])
                if hits and not (subject and _found(subject, sentence)):
                    out.append(_flag("rule:filler", shot,
                                     f"\"{sentence[:60]}\" ({', '.join(hits)}) comes before "
                                     "the first sentence about the subject",
                                     "open on the subject; cut the greeting, the "
                                     "introduction or the call to subscribe"))
                else:
                    content_started = True
            if len(words) > BOUNDS["sentence"]["value"]:
                out.append(_flag("rule:sentence", shot,
                                 f"a sentence of {len(words)} words, over "
                                 f"{BOUNDS['sentence']['value']}: \"{sentence[:60]}...\"",
                                 "split it where a breath falls; one idea a sentence"))
            figures = _NUMERAL.findall(sentence)
            if len(figures) >= BOUNDS["numbers"]["value"]:
                out.append(_flag("rule:numbers", shot,
                                 f"{len(figures)} numerals in one sentence "
                                 f"({', '.join(figures)})",
                                 "one figure a sentence; round the other or give it "
                                 "its own sentence"))
            if on["rule:clause"]:
                low = " " + " ".join(sentence.lower().split()) + " "
                marks = sum(low.count(f" {w} ") for w in lex["relative"])
                marks += sum(low.count(f", {w} ") for w in lex["joins"]) + low.count(";")
                if 1 + marks > BOUNDS["clause"]["value"]:
                    out.append(_flag("rule:clause", shot,
                                     f"{1 + marks} clauses in one sentence: "
                                     f"\"{sentence[:60]}\"",
                                     "end the sentence before the which, that or who "
                                     "and start a new one"))

    # -- what is on screen: legibility, unspoken figures --------------------
    by_label = {shot.label: shot for _, shot, _ in timed}
    for row in legibility(sections):
        out.append(_flag("rule:legibility", by_label.get(row["label"]),
                         f"\"{row['text']}\" is {row['chars']} characters in "
                         f"{row['seconds']} s ({row['cps']} a second, over "
                         f"{READING_CPS:.0f}); it needs {row['needs_seconds']} s",
                         "shorten the on-screen text"))
    if on["rule:unspoken-number"]:
        for row in unspoken_numbers(sections):
            out.append(_flag("rule:unspoken-number", by_label.get(row["label"]),
                             f"the screen shows {row['number']}, which the narration "
                             "never says",
                             "say the figure in the line, or take it off the screen"))

    # -- the opening --------------------------------------------------------
    window = BOUNDS["hook_window"]["value"]
    hook = [(sec, shot, at) for sec, shot, at in timed if at < window]
    hook_text = " ".join(spoken(s.narration) + " " + " ".join(s.on_screen_text or [])
                         for _, s, _ in hook)
    first = next((shot for _, shot, at in voiced if at < window), None)
    if on["rule:hook-subject"] and subject and first is not None \
            and not _found(subject, hook_text):
        out.append(_flag("rule:hook-subject", first,
                         f"none of the title's words ({', '.join(subject)}) is said "
                         f"or shown in the first {window} s",
                         "name the subject the title promised in the opening lines"))
    ending = plan.get("ending") if isinstance(plan.get("ending"), dict) else {}
    payoff_line = str(ending.get("payoff_line") or "").strip()
    norm = lambda text: " ".join(_words(text))  # noqa: E731
    answers = [norm(s) for text in (payoff_line, str(plan.get("payoff") or ""))
               for s in sentences(text)
               if len(_words(s)) >= BOUNDS["hook_answer"]["value"]]
    for _, shot, at in hook:
        if not answers or shot.function == "payoff" or not spoken(shot.narration):
            continue
        said = [norm(s) for s in sentences(spoken(shot.narration))]
        if any(a in s for a in answers for s in said):
            out.append(_flag("rule:hook-answer", shot,
                             "the opening says the film's payoff verbatim",
                             "withhold the answer; tease it, and say it where it is "
                             "paid"))
    promise = _content(str(plan.get("promise") or ""), lex)
    if on["rule:hook-promise"] and promise and first is not None:
        share = len(_found(promise, hook_text)) / len(promise)
        if share < BOUNDS["hook_promise"]["value"]:
            out.append(_flag("rule:hook-promise", None,
                             f"the first {window} s carry {share:.0%} of the promise's "
                             f"words ({', '.join(promise)})",
                             "let the opening say what the film promises"))
    stakes_window = BOUNDS["hook_stakes"]["value"]
    early = " ".join(spoken(s.narration) for _, s, at in voiced if at < stakes_window)
    if on["rule:hook-stakes"] and early and not _has(early, lex["viewer"]):
        out.append(_flag("rule:hook-stakes", None,
                         f"no line in the first {stakes_window} s is addressed to the "
                         "viewer",
                         "say what it costs the viewer, to them"))

    # -- section by section: person, pace, density ---------------------------
    seen_names: set[str] = set()
    for section in sections:
        lines = [s for s in section.shots if spoken(s.narration)]
        if not lines:
            continue
        text = " ".join(spoken(s.narration) for s in lines)
        words = len(text.split())
        if on["rule:person"] and not _has(text, lex["person"]):
            out.append(_flag("rule:person", lines[0],
                             f"section \"{section.heading}\" never addresses the viewer",
                             "speak to the viewer: you, or we"))
        seconds = sum(float(s.seconds or 0) for s in lines)
        if seconds > 0 and wpm > 0:
            needs = words * 60 / wpm
            if needs > seconds * BOUNDS["pace"]["value"]:
                out.append(_flag("rule:pace", lines[0],
                                 f"section \"{section.heading}\": {words} words take "
                                 f"{needs:.0f} s at {wpm} wpm (--wpm), over the "
                                 f"{seconds:.0f} s "
                                 f"its narrated shots are planned for (a planned pace "
                                 f"of {words * 60 / seconds:.0f} wpm); voice and retime "
                                 "measure the read",
                                 "cut words, or give the shots more time"))
        if on["rule:density"] and words:
            names = 0
            for sentence in sentences(text):
                for token in sentence.split()[1:]:
                    token = token.strip(".,;:!?\"'()“”‘’")
                    if token and token != "I" and _CAPITALISED.match(token) \
                            and token.lower() not in seen_names:
                        seen_names.add(token.lower())
                        names += 1
            dense = (len(_NUMERAL.findall(text)) + names) * 100 / words
            if dense > BOUNDS["density"]["value"]:
                out.append(_flag("rule:density", lines[0],
                                 f"section \"{section.heading}\" carries {dense:.0f} "
                                 "numerals and new names per 100 words",
                                 "thin the facts, or give them more time"))

    # -- the film as a whole: passive ---------------------------------------
    if on["rule:passive"]:
        said = [(shot, s) for _, shot, _ in voiced for s in sentences(spoken(shot.narration))]
        passive = [(shot, s) for shot, s in said if _PASSIVE.search(s)]
        if said and len(passive) / len(said) > BOUNDS["passive"]["value"]:
            out.append(_flag("rule:passive", None,
                             f"{len(passive)} of {len(said)} sentences are passive "
                             f"(shots {', '.join(dict.fromkeys(s.label for s, _ in passive))})",
                             "say who does it: subject, verb, object"))
        for shot, s in passive:
            if shot.function == "payoff":
                out.append(_flag("rule:passive", shot,
                                 f"the payoff is passive: \"{s[:60]}\"",
                                 "say who does it"))

    # -- the ending ----------------------------------------------------------
    last = next((sec for sec in reversed(sections)
                 if any(spoken(s.narration) for s in sec.shots)), None)
    if last is not None:
        tail_shot = [s for s in last.shots if spoken(s.narration)][-1]
        payoff_words = _content(payoff_line, lex)
        if on["rule:payoff-last"] and payoff_words:
            text = " ".join(spoken(s.narration) for s in last.shots)
            share = len(_found(payoff_words, text)) / len(payoff_words)
            if share < BOUNDS["payoff_last"]["value"]:
                out.append(_flag("rule:payoff-last", tail_shot,
                                 f"the last section carries {share:.0%} of the payoff "
                                 f"line's words ({', '.join(payoff_words)})",
                                 "end on the payoff line, the title's promise paid"))
        closing = sentences(spoken(tail_shot.narration))[-1]
        if len(closing.split()) > BOUNDS["last_line"]["value"]:
            out.append(_flag("rule:last-line", tail_shot,
                             f"the film's last sentence is {len(closing.split())} words",
                             "close on one short line"))
    order = [shot for _, shot, _ in timed]
    paid = [i for i, s in enumerate(order) if s.function == "payoff"]
    if on["rule:end-signal"] and runtime:
        start = runtime * (1 - BOUNDS["end_signal"]["value"])
        for i, (sec, shot, at) in enumerate(timed):
            if at + float(shot.seconds or 0) <= start or not spoken(shot.narration):
                continue
            hits = _has(shot.narration, lex["end"])
            if set(hits) <= set(lex["finally"]) and hits:
                before = " ".join(spoken(s.narration) for s in sec.shots)
                cut = before.lower().find(hits[0])
                if _has(before[:cut], lex["first"]):
                    hits = []           # "first ... then ... finally": an enumeration
            if hits and not any(p >= i for p in paid):
                out.append(_flag("rule:end-signal", shot,
                                 f"\"{hits[0]}\" signals the end with no payoff after it",
                                 "cut the signal, or let it lead into the payoff"))
    if on["rule:recap"] and paid:
        after = [s for s in order[paid[-1] + 1:] if spoken(s.narration)]
        words = sum(len(spoken(s.narration).split()) for s in after)
        signed = [(s, h) for s in after for h in _has(s.narration, lex["sign_off"])]
        if words > BOUNDS["recap"]["value"]:
            out.append(_flag("rule:recap", after[0],
                             f"{words} words of narration after the payoff shot",
                             "end within a line of the payoff"))
        elif signed:
            out.append(_flag("rule:recap", signed[0][0],
                             f"\"{signed[0][1]}\" after the payoff",
                             "end on the payoff; no sign-off"))
    return out


def rule_lines(flags: list[dict], kept: dict | None = None, lang: str = "",
               *, revised: bool = True) -> list[str]:
    """script.json's `findings` for the rules, in sdkkit's prefixes: an
    error-tier flag still standing is `error: rule:x`, the important and
    minor ones `warning: rule:x`, a house one `warning: house:rule:x`; a
    flag the revision kept carries its reason. Then a `not_evidenced:` line
    for each rule the language takes out."""
    kept = kept or {}
    out = []
    for f in flags:
        tier = f.get("tier") or _RULE[f["rule"]].tier
        line = f["problem"]
        why = kept.get((f["rule"], f.get("n", 0)))
        if why:
            line += f"; kept: {why}"
        elif tier in ("error", "important"):
            line += ("; still standing after the revision" if revised
                     else "; not revised (--report-only)")
        if tier == "error":
            out.append(f"error: {line}")
        elif tier == "house":
            out.append(f"warning: house:{line}")
        else:
            out.append(f"warning: {line}")
    return out + not_evidenced(lang)


#: The order findings are printed in: what blocks, what warns, the rest.
_PREFIX_ORDER = ("error", "warning")


def print_hints(hints: dict) -> None:
    """What the house standards can be checked for, rather than asked for:
    script.json's `findings` (the narration rules' and the stage's notes),
    errors first. A screenplay written before the rules has only the
    legibility and unspoken-figure tables, which are printed as they were."""
    findings = [str(f) for f in hints.get("findings") or []]
    if findings:
        rank = lambda f: next((i for i, p in enumerate(_PREFIX_ORDER)  # noqa: E731
                               if f.startswith(p + ":")), len(_PREFIX_ORDER))
        print(f"\n\n{'=' * 78}\nFINDINGS")
        for finding in sorted(findings, key=rank):
            print(wrap(f"· {finding}", "  "))
        return
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
    for key in ("train", "stakes"):
        if data.get(key):
            print(wrap(f"{key}: {data[key]}", "  "))
    review = data.get("plan_review") if isinstance(data.get("plan_review"), dict) else {}
    if review.get("verdict"):
        print(wrap(f"plan review: {review['verdict']}"
                   + (" (planned once more)" if review.get("retried") else ""), "  "))
    story = data.get("review") if isinstance(data.get("review"), dict) else {}
    if story.get("verdict"):
        grades = ", ".join(f"{k} {v}" for k, v in (story.get("grades") or {}).items())
        print(wrap(f"story review: {story['verdict']}"
                   + (f" ({grades})" if grades else ""), "  "))

    for section in sections:
        print(f"\n\n{'=' * 78}\n{section.heading.upper()}  "
              f"[{timecode(section.shots[0].at) if section.shots else '-'}, "
              f"{section.actual_seconds}s, {section.words} words]"
              + (f"  {section.connective.upper()}" if section.connective else ""))
        if section.purpose:
            print(wrap(section.purpose, "  "))
        for shot in section.shots:
            print(f"\n  {shot.label}  {timecode(shot.at)}  ({shot.seconds}s)"
                  + (f"  [{shot.style}]" if shot.style else "")
                  + (f"  {shot.function}" if shot.function else ""))
            print(wrap(f"VIDEO  {shot.visual}", "    "))
            if shot.shows:
                print(wrap(f"SHOWS  {shot.shows}", "    "))
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

    # What the stage recorded instead of deciding (a value off a schema
    # list) and what the narration rules found, so the operator sees it
    # without opening script.json.
    print_hints(data)


def question_ledger(questions) -> list[str]:
    """The plan's questions as a Markdown table: where each opens and where
    it is answered, so an unanswered one is visible on the page."""
    rows = [q for q in questions or [] if isinstance(q, dict)]
    if not rows:
        return []
    cell = lambda v: " ".join(str(v if v is not None else "").split()).replace("|", "/")  # noqa: E731

    def answered(q: dict) -> str:
        # closed_in 0 is a question left open on purpose (structure 8)
        if q.get("closed_in") == 0:
            why = cell(q.get("left_open_why"))
            return "left open" + (f": {why}" if why else "")
        return cell(q.get("closed_in"))
    return (["## Question ledger", "", "| id | question | opened in | answered in |",
             "|---|---|---|---|"]
            + [f"| {cell(q.get('id'))} | {cell(q.get('text'))} | {cell(q.get('opened_in'))} "
               f"| {answered(q)} |" for q in rows] + [""])


def plan_review_lines(record) -> list[str]:
    """script.json's `plan_review` as a Markdown section: who read the plan,
    the verdict, whether it was planned again, the problems and the
    pre-checks the reviewer was shown."""
    if not isinstance(record, dict) or not record.get("verdict"):
        return []
    md = ["## Plan review", "",
          f"**Verdict.** {record['verdict']}"
          + (f", read by {record['model']}" if record.get("model") else "")
          + ("; planned once more after the first reading sent it back"
             if record.get("retried") else ""), ""]
    rounds = [r for r in record.get("rounds") or [] if isinstance(r, dict)]
    for r in rounds[:-1]:
        # what sent the first plan back, kept beside the final reading
        md += [f"- {r.get('name', 'plan-review')}: {r.get('verdict', '?')}"]
        md += [f"  - {line}" for line in problem_lines(r.get("problems"))]
    md += [f"- {line}" for line in problem_lines(record.get("problems"))]
    md += [f"- pre-check: `{c.split(':', 1)[0]}` {c.split(':', 1)[-1].strip()}"
           for c in record.get("prechecks") or []]
    # what the reading still contradicted after it was asked again
    md += [f"- unsettled: {line}" for line in record.get("unsettled") or []]
    return md + [""]


def write_md(path: str, idea: Idea, data: dict, sections: list[Section]) -> None:
    shots = [s for sec in sections for s in sec.shots]
    md = [f"# {idea.title}", "",
          f"`{idea.genre}` · target **{timecode(data['target_seconds'])}** · "
          f"{sum(s.words for s in shots)} words at {data['wpm']} wpm · "
          f"{len(shots)} shots", ""]
    for key, label in (("logline", "Logline"), ("train", "Train"),
                       ("promise", "The hook promises"), ("payoff", "Paid off by"),
                       ("stakes", "Stakes"), ("viewer_stake", "For the viewer"),
                       ("misconception", "The belief it breaks"),
                       ("misconception_why", "No misconception, because"),
                       ("audience", "Audience")):
        if data.get(key):
            md += [f"**{label}.** {data[key]}", ""]
    reveal = data.get("reveal") if isinstance(data.get("reveal"), dict) else {}
    if reveal.get("section"):
        md += [f"**Reveal.** section {reveal['section']}, after the constraint in "
               f"section {reveal.get('constraint_section', '?')}", ""]
    md += question_ledger(data.get("questions"))

    for n, section in enumerate(sections, 1):
        start = timecode(section.shots[0].at) if section.shots else "-"
        md += [f"## {n}. {section.heading}", "",
               f"*{start} · {section.actual_seconds}s · {section.words} words*"
               + (f" — {section.purpose}" if section.purpose else ""), ""]
        story = [(f"**{section.connective.upper()}**" if section.connective else ""),
                 (f"opens {', '.join(section.opens)}" if section.opens else ""),
                 (f"answers {', '.join(section.closes)}" if section.closes else ""),
                 (f"re-opens {section.rehook}" if section.rehook else ""),
                 (f"escalates: {section.escalation}" if section.escalation else ""),
                 (f"stands on: {section.anchor_visual}" if section.anchor_visual else "")]
        if any(story):
            md += [" · ".join(s for s in story if s), ""]
        for shot in section.shots:
            md += [f"### {shot.label} · {timecode(shot.at)} ({shot.seconds}s)"
                   + (f" · `{shot.style}`" if shot.style else "")
                   + (f" · *{shot.function}*" if shot.function else ""), "",
                   f"**VIDEO** — {shot.visual}", ""]
            if shot.shows:
                md += [f"**SHOWS** — {shot.shows}", ""]
            if shot.narration:
                md += [f"**AUDIO** — {shot.narration}", ""]
            extras = [(f"**SUPER** — {shot.on_screen}" if shot.on_screen else ""),
                      (f"**ASSET** — {shot.asset}" if shot.asset else ""),
                      (f"**CITE** — {shot.source}" if shot.source else ""),
                      (f"**NOTE** — {shot.note}" if shot.note else ""),
                      (f"*polished: {shot.revised}*" if shot.revised else "")]
            md += [e for e in extras if e]
            md.append("")

    ending = data.get("ending") if isinstance(data.get("ending"), dict) else {}
    if any(ending.get(k) for k in ("payoff_line", "callback", "next_film")):
        md += ["## Ending", "",
               f"- **Payoff line:** {ending.get('payoff_line', '')}",
               f"- **Callback:** {ending.get('callback', '')}",
               f"- **Next film:** {ending.get('next_film', '')}", ""]

    md += plan_review_lines(data.get("plan_review"))
    md += story_review_lines(data.get("review"))

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

    if data.get("findings"):
        md += ["## Findings", "",
               "The narration rules' findings (`error:` blocks the stage, "
               "`warning:` does not, `house:` is the house's own prior) and the "
               "stage's notes.", ""]
        md += [f"- `{f.split(': ', 1)[0]}` {f.split(': ', 1)[-1]}"
               for f in data["findings"]]
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
    cols = ["label", "at", "seconds", "section", "function", "style", "visual",
            "shows", "narration", "on_screen", "asset", "source", "note"]
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
                   help="fact-check the script against the dossier, fix the "
                        "important and blocking lines, and re-check them")
    p.add_argument("--report-only", action="store_true",
                   help="report the findings without fixing them: the fact-check's "
                        "(with --verify), the narration rules' and the plan review's "
                        "(a revise is recorded, not planned again; a reject still "
                        "stops); a standing `error:` rule finding then does not stop "
                        "the stage")
    # vfx imports this module, so its reviewer defaults are read here, when
    # the parser is built, not at import.
    from .vfx import REVIEW_USD, REVIEWER_MODEL
    p.add_argument("--review", action="store_true",
                   help="have a reviewer of another model family read the plan before "
                        "any section is drafted (plan-review): accept drafts, revise "
                        "plans once more with the problems, reject or a second revise "
                        "stops the stage with exit 1; then read the drafted script "
                        f"before voice: {STORY_SAMPLES} samples a round (review-r1-s1..), "
                        "a cold read of the narration alone, and a consider's flags "
                        "sent back (revise-story-N, redraft-NN). A reject is an error: "
                        "finding; it stops the stage only with --gate")
    p.add_argument("--gate", action="store_true",
                   help="with --review: a story review that rejects, or that has too "
                        "few valid samples to judge (not_evidenced), leaves "
                        f"narration.txt unwritten and exits 1. Off by default: {GATE_WHEN}")
    flags.fresh(p, help="ask every decision again instead of reusing the ones on disk")
    flags.reviewer_model(p, default=REVIEWER_MODEL,
                         help="the model that reviews the plan and the story with "
                              f"--review, never the author's own (default {REVIEWER_MODEL})")
    flags.review_budget(p, default=REVIEW_USD,
                        help=f"what one review may cost (default {REVIEW_USD:g})")
    flags.rounds(p, default=2,
                 help="revision rounds of the fact-check with --verify and of the story "
                      "review with --review, each (default 2); the plan review sends a "
                      "plan back once whatever this says")
    p.add_argument("--revise", default="", metavar="LABELS",
                   help="send these shots back for one revision instead of writing "
                        "the screenplay again: comma-separated labels (1.3,2.1), "
                        "`retime` for every line retime.json sends back to be "
                        "rewritten, or `feedback` for the shots feedback/script.json "
                        "names. Only those lines change; they are measured against "
                        "the rules and fact-checked again (with research), and the "
                        "run prints the `yt voice --redo` their takes need")
    p.add_argument("--why", default="", metavar="TEXT",
                   help="with --revise: the reason, handed to the revision with "
                        "each line")
    p.add_argument("--jobs", type=int, default=4,
                   help="sections drafted at the same time (default 4; 1 writes "
                        "them in order, each reading the one before)")
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
    # A model never grades its own work: refused before anything is paid.
    if args.review and (own := flags.own_reviewer(args.model, args.reviewer_model)):
        print(own, file=sys.stderr)
        return 2

    split = lambda s: [p.strip() for p in s.split(",") if p.strip()]  # noqa: E731
    idea, project = workspace.resolve(args, inputs=("dossier", "direction"))
    if args.revise:
        return run_revise(args, idea, project)

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
        idea, house=rules, house_digest=digest, root=project.root,
        model=args.model, minutes=args.minutes, wpm=args.wpm,
        sections=args.sections, inspo=split(args.inspo), notes=args.notes,
        lang=args.lang, jobs=args.jobs,
        research=research_block(dossier) if dossier else "",
        direction=direction_block(direction) if direction else "",
        outline=(dossier or {}).get("outline") if args.outline else None,
        reviewer_model=args.reviewer_model if args.review else None,
        review_budget_usd=args.review_budget_usd, rounds=args.rounds,
        fresh=getattr(args, "fresh", False),
        review_log=review_log_path(args, project) if args.review else None,
        verbose=not args.quiet)

    stopped = Path(project.out("script", args.json_out)).with_name(PLAN_REVIEW_FILE)
    try:
        data = writer.run(do_polish=args.polish, do_verify=args.verify,
                          fix=not args.report_only, review=args.review)
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        # What was asked before the stop was paid for; say so.
        print(f"{writer.usage}", file=sys.stderr)
        if writer.plan_review_stop is not None:
            write_json(stopped, {"plan_review": writer.plan_review_stop,
                                 "usage": spend.from_usage(writer.usage, writer.model),
                                 "engine": writer.harness.report()})
            print(f"wrote {stopped}", file=sys.stderr)
        return 1
    if stopped.exists():
        # The stage got past its plan this time: script.json is the record
        # now (its `plan_review` when the plan was read), so the stop is gone.
        stopped.unlink()

    sections = [Section.from_spec(s, [Shot(**shot) for shot in s["shots"]])
                for s in data["sections"]]
    shots = [s for sec in sections for s in sec.shots]
    if not shots:
        print("error: no shots were written", file=sys.stderr)
        return 1

    # The two checks before the rules, kept under their old keys for one
    # release beside `findings`, where they are rule:legibility and
    # rule:unspoken-number now.
    hints = {"legibility": legibility(sections),
             "unspoken_numbers": unspoken_numbers(sections)}
    data |= hints

    print_script(idea, data, sections)
    errors, blocked = write_outputs(args, project, idea, data, sections, wpm=args.wpm)

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
    gated = story_gate(args, data)
    if blocked:
        if errors:
            print(f"error: {len(errors)} narration rule finding(s) of the error tier "
                  "stand; narration.txt was not written. Run `yt script` again with "
                  "--notes saying how to clear them, or --report-only to record them "
                  "and go on.", file=sys.stderr)
        if gated:
            print(f"error: {gated}; narration.txt was not written (--gate). Run "
                  f"`{REPLAN_FIX}` with --notes saying what the review asks, or "
                  "drop --gate to record the verdict and go on.", file=sys.stderr)
        return 1
    verdict = (data.get("review") or {}).get("verdict")
    if verdict in ("reject", "not_evidenced"):
        print(f"warning: story review: {verdict}; narration.txt was written, since "
              f"--gate is off ({GATE_WHEN}). Doctor fails on a reject.", file=sys.stderr)
    return 1 if data["failed_sections"] else 0


def review_log_path(args, project) -> Path:
    """work/review-log.jsonl: the work directory named on the command line,
    else the one the project sits in."""
    work = getattr(args, "work", None)
    return (Path(work) if work else Path(project.root).parent) / REVIEW_LOG


def story_gate(args, data: dict) -> str:
    """What stops voice under `--gate`: a story review that rejected, or one
    that could not judge (not_evidenced). Empty without --gate, with
    --report-only, or when the review passed."""
    verdict = (data.get("review") or {}).get("verdict")
    if not getattr(args, "gate", False) or getattr(args, "report_only", False):
        return ""
    return f"story review: {verdict}" if verdict in ("reject", "not_evidenced") else ""


def write_outputs(args, project, idea: Idea, data: dict, sections: list[Section],
                  *, wpm: int) -> tuple[list[str], bool]:
    """script.md, narration.txt, shotlist.csv and script.json from `data`.

    A narration rule of the error tier still standing after its revision
    blocks the stage: the prompter a narrator reads from is not written (an
    older one is removed, so it cannot be read by mistake) and the caller
    exits 1. --report-only records it and goes on. The story review's
    reject is an `error:` too, but it blocks only under `--gate`
    (story_gate): until the reviewer is calibrated it is evidence, not a
    gate. Returns the standing rule errors and whether anything blocked."""
    errors = [f for f in data.get("findings", []) if str(f).startswith("error:")
              and not str(f).startswith("error: story review:")]
    blocked = (bool(errors) and not args.report_only) or bool(story_gate(args, data))
    narration = project.out("narration", args.vo_out)
    for path, writer_fn in (
            (project.out("script_md", args.md_out),
             lambda p: write_md(p, idea, data, sections)),
            (narration, lambda p: write_prompter(p, idea, sections, wpm=wpm)),
            (project.out("shotlist", args.csv_out),
             lambda p: write_csv(p, sections)),
            (project.out("script", args.json_out),
             lambda p: write_json(p, data))):
        if blocked and path == narration:
            if Path(path).exists():
                Path(path).unlink()
                print(f"removed {path}: it no longer matches the screenplay",
                      file=sys.stderr)
            continue
        writer_fn(path)
        print(f"wrote {path}", file=sys.stderr)
    return errors, blocked


# --------------------------------------------------------------------------- #
# --revise: send named lines back, not the screenplay (AUTO-95)
# --------------------------------------------------------------------------- #

#: Who sent the lines back, as the revision is told it, by where they came from.
SENT_BY = {
    "labels": "the operator",
    "retime": "the editor, after the read was recorded and measured (yt retime)",
    "feedback": "QA's review of the rendered film (yt improve)",
}


def sent_back(spec: str, root: Path, why: str = "") -> tuple[str, list[dict], list[str]]:
    """The lines `--revise` names: where they came from (`labels`, `retime`,
    `feedback`), one row per reason ({label, problem, fix}), and notes on
    what was named and cannot be revised. A missing file is a ValueError.

    `retime` reads retime.json's `rewrite` decisions with what each read
    measured; `feedback` reads the `.json` twin `yt improve` writes beside the
    brief, never the prose, and takes the findings that name a shot."""
    spec = spec.strip()
    root = Path(root)
    rows: list[dict] = []
    notes: list[str] = []
    if spec == "retime":
        report = read_json(root / "retime.json", warn=False)
        if not isinstance(report, dict):
            raise ValueError("no retime.json to take the rewrites from; run `yt retime` "
                             "first, or name the shots")
        measured = {c.get("label"): c for c in report.get("changes") or []
                    if isinstance(c, dict)}
        for d in report.get("decisions") or []:
            if not (isinstance(d, dict) and d.get("action") == "rewrite" and d.get("label")):
                continue
            label = str(d["label"])
            change = measured.get(label) or {}
            problem = f"the editor: {str(d.get('note') or '').strip() or 'rewrite it'}"
            fix = "fit the line to its shot"
            if change.get("measured") is not None and change.get("from") is not None:
                problem = (f"the read measured {change['measured']} s against the "
                           f"{change['from']} s planned for its shot; " + problem)
                fix = (f"cut it to fit {change['from']} s"
                       if float(change["measured"]) > float(change["from"])
                       else f"give it the words {change['from']} s holds")
            rows.append({"label": label, "problem": problem, "fix": fix})
    elif spec == "feedback":
        twin = read_json(root / harness.FEEDBACK_DIR / "script.json", warn=False)
        if not isinstance(twin, dict):
            raise ValueError(f"no {harness.FEEDBACK_DIR}/script.json: `yt improve` writes "
                             "it beside the brief; name the shots instead")
        for f in twin.get("findings") or []:
            if not isinstance(f, dict):
                continue
            label = str(f.get("shot") or "").strip()
            if not label:
                notes.append(f"{f.get('id') or 'a finding'} names no shot; a full "
                             "`yt script` run is what answers it")
                continue
            problem = str(f.get("problem") or "").strip()
            if f.get("why"):
                problem += f" (why: {str(f['why']).strip()})"
            # A finding with no fix of its own is not handed the brief's
            # whole must-change list: that names changes to other shots.
            rows.append({"label": label, "problem": problem,
                         "fix": str(f.get("fix") or "").strip()
                         or "the change the reason asks for"})
    else:
        for label in (p.strip() for p in spec.split(",")):
            if label:
                rows.append({"label": label,
                             "problem": why or "the operator sent this line back",
                             "fix": "the smallest change that lets it be said aloud "
                                    "in one breath, its key term not buried"})
        return "labels", rows, notes
    if why:
        for row in rows:
            row["problem"] += f"; the operator adds: {why}"
    return spec, rows, notes


def run_revise(args, idea: Idea, project) -> int:
    """`yt script --revise`: one `revise-labels` decision over the named
    shots, then the narration rules on the changed shots only, and the
    fact-check of the changed lines (`verify-labels`) when there is
    research. Every other shot is written back exactly as it was read;
    nothing is planned or drafted again, and the takes are voice's to
    record (the run prints the `yt voice --redo` they need)."""
    path = project.out("script", args.json_out)
    data = read_json(path, warn=False) if Path(path).is_file() else None
    if not isinstance(data, dict) or not data.get("sections"):
        print("error: no screenplay to revise; run `yt script` first", file=sys.stderr)
        return 1
    sections = sections_of(data)
    shots = [s for sec in sections for s in sec.shots]
    by_label = {s.label: s for s in shots}
    numbers = [s.n for s in shots]
    if 0 in numbers or len(set(numbers)) != len(numbers):
        print("error: the screenplay's shots are not numbered; run `yt script` "
              "again before revising single lines", file=sys.stderr)
        return 1
    try:
        source, rows, notes = sent_back(args.revise, project.root, args.why)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    unknown = list(dict.fromkeys(r["label"] for r in rows if r["label"] not in by_label))
    if unknown and source == "labels":
        print(f"error: no shot {', '.join(unknown)} in the screenplay (its shots run "
              f"{shots[0].label} to {shots[-1].label}); nothing was revised",
              file=sys.stderr)
        return 2
    notes += [f"shot {label} is not in the screenplay; skipped" for label in unknown]
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    rows = [r for r in rows if r["label"] in by_label]
    if not rows:
        print(f"nothing to revise: {args.revise} names no shot of the screenplay",
              file=sys.stderr)
        return 0
    for ignored in ("polish", "verify", "outline"):
        if getattr(args, ignored, False):
            print(f"note: --revise changes only the named lines; --{ignored} is not "
                  "used", file=sys.stderr)

    named = set(r["label"] for r in rows)
    targets = [s for s in shots if s.label in named]
    flags = [{"n": by_label[r["label"]].n, "severity": "important",
              "problem": r["problem"], "fix": r["fix"]} for r in rows]
    dossier = read_json(project.source("dossier", args.dossier))
    rules, digest = house.for_args(args, project, "script")
    wpm = int(data.get("wpm") or args.wpm)
    lang = args.lang or str(data.get("lang") or "")
    writer = Screenwriter(
        idea, house=rules, house_digest=digest, root=project.root, model=args.model,
        minutes=float(data.get("target_seconds") or 60) / 60, wpm=wpm, lang=lang,
        research=research_block(dossier) if dossier else "", verbose=not args.quiet)

    # Every shot goes in, to read the named lines in; only the flagged
    # numbers are answered, and only they can change.
    result = writer.revise(shots, flags, name="revise-labels", back=SENT_BY[source])
    if result.get("failed"):
        print("error: the revision gave no answer; nothing was written", file=sys.stderr)
        return 1
    answered = {r["n"] for r in result["applied"]} | {k["n"] for k in result["kept"]}
    kept = result["kept"] + [{"n": s.n, "label": s.label,
                              "why": "the revision left it as written"}
                             for s in targets if s.n not in answered]
    # `applied` holds only rows whose text changed: the narration (its take
    # is stale and the fact-check reads it) or the supers (the rules
    # measure them, the take still holds).
    changed = {r["n"] for r in result["applied"]}
    moved = [s for s in targets if s.n in {r["n"] for r in result["applied"]
                                            if "before" in r}]

    # The fact-check is never skipped on a changed line when there is
    # research to hold it to, and what it flags goes to the fact revision
    # and is checked again, as in the stage's own loop. A check that failed
    # leaves everything as it was.
    again: dict = {}
    if moved and writer.research:
        again = writer.verify_and_revise(moved, tag="labels")
        if not again:
            print("error: the fact-check of the changed lines failed; nothing was "
                  "written", file=sys.stderr)
            return 1

    # Only the changed shots are written into the record as read: every
    # other shot stays the dict it was, byte for byte.
    raw = {shot.get("label"): shot for sec in data["sections"] for shot in sec["shots"]}
    screen = {r["n"] for r in result["applied"] if "on_screen_before" in r}
    for s in targets:
        if s.n in changed:
            raw[s.label]["narration"] = s.narration
            raw[s.label]["revised"] = s.revised
        if s.n in screen:
            raw[s.label]["on_screen_text"] = list(s.on_screen_text or [])

    # The rules on the changed shots only: their old flags go, what the
    # new text breaks comes in; every other flag stands as it was recorded.
    ruled = data.get("rules") if isinstance(data.get("rules"), dict) else {}
    old = [f for f in ruled.get("flags") or [] if isinstance(f, dict)]
    stale = [f["problem"] for f in old if f.get("n") in changed and f.get("problem")]
    fresh = [f for f in rule_findings(data, sections, wpm, lang) if f["n"] in changed]
    data["findings"] = ([line for line in data.get("findings") or []
                         if not any(p in str(line) for p in stale)]
                        + [line for line in rule_lines(fresh)
                           if not line.startswith("not_evidenced:")])
    data["rules"] = {**ruled, "flags": [f for f in old if f.get("n") not in changed] + fresh,
                     "kept": [k for k in ruled.get("kept") or []
                              if isinstance(k, dict) and k.get("n") not in changed]}
    if again:
        # The changed lines' flags, and the reasons an older line was kept,
        # are the new check's: they spoke of text that is gone.
        renewed = {s.n for s in moved}
        check = dict(data.get("check") or {})
        check["flags"] = ([f for f in check.get("flags") or []
                           if int(f.get("n", 0) or 0) not in renewed]
                          + [f for f in again.get("flags") or []
                             if int(f.get("n", 0) or 0) in renewed])
        check["kept"] = ([k for k in check.get("kept") or []
                          if isinstance(k, dict) and int(k.get("n", 0) or 0) not in renewed]
                         + list(again.get("kept") or []))
        check["revisions"] = list(check.get("revisions") or []) + list(
            again.get("revisions") or [])
        check["recheck"] = again.get("recheck") or again.get("verdict", "")
        check["unresolved"] = [f for f in check["flags"]
                               if f.get("severity") in REVISABLE and not f.get("kept")]
        data["check"] = check
    for key, measure in (("legibility", legibility), ("unspoken_numbers", unspoken_numbers)):
        if key in data:
            data[key] = measure(sections)
    data.setdefault("revised", []).append({
        "from": source, "asked": [s.label for s in targets], "why": args.why,
        "applied": result["applied"], "kept": kept,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    # What this run spent; the draft's bill was its own run's.
    data["usage"] = spend.from_usage(writer.usage, writer.model)
    data["engine"] = writer.harness.report()

    backup = Path(path).with_suffix(".json.bak")
    shutil.copy2(path, backup)
    errors, blocked = write_outputs(args, project, idea, data, sections, wpm=wpm)
    print(f"the screenplay before this revision is kept at {backup}", file=sys.stderr)

    print(f"\n{idea.title.upper()}: REVISED FROM {source.upper()}")
    for row in result["applied"]:
        print(wrap(f"changed {row['label']}: {row['why']}", "  "))
        if "before" in row:
            print(wrap(f"was: {row['before']}", "      "))
            print(wrap(f"now: {row['after']}", "      "))
        if "on_screen_before" in row:
            print(wrap(f"on screen was: {' | '.join(row['on_screen_before'])}", "      "))
            print(wrap(f"on screen now: {' | '.join(row['on_screen_after'])}", "      "))
    for row in kept:
        print(wrap(f"kept {row['label']}: {row['why']}", "  "))
    for row in (again.get("revisions") or []) if again else []:
        print(wrap(f"fact-check changed {row['label']}: {row['why']}", "  "))
    labels = {s.n: s.label for s in shots}
    for f in (again.get("flags") or []) if again else []:
        if int(f.get("n", 0) or 0) in labels:
            kept_why = f" (kept: {f['kept']})" if f.get("kept") else ""
            print(wrap(f"fact-check {labels[int(f['n'])]} ({f.get('severity', '?')}): "
                       f"{f.get('problem', '')}{kept_why}", "  "))
    for line in rule_lines(fresh):
        if not line.startswith("not_evidenced:"):
            print(wrap(line, "  "))
    print(f"{writer.usage}", file=sys.stderr)
    if moved:
        print(f"\nthe changed lines' takes are stale; record them again: "
              f"yt voice --redo {','.join(s.label for s in moved)} "
              f"--project {project.root}, then `yt retime`")
    if blocked:
        if errors:
            print(f"error: {len(errors)} narration rule finding(s) of the error tier "
                  "stand; narration.txt was not written. Revise those shots again "
                  "(--revise with --why), or --report-only to record them and go on.",
                  file=sys.stderr)
        if story_gate(args, data):
            print(f"error: {story_gate(args, data)} stands from the last review; "
                  f"narration.txt was not written (--gate). Run `{REPLAN_FIX}`.",
                  file=sys.stderr)
        return 1
    return 0
