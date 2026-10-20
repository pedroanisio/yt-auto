#!/usr/bin/env python3
"""Deep-research one video idea into a production dossier.

Takes an idea from `yt ideas` (or typed in directly), runs staged web research
through claude_engine, and writes everything to disk: claims with the source
each came from, a vetted source list, multimedia assets with licences, the gaps
that still need checking, and a script outline that cites both.

    yt research --from ideas.json --index 1
    yt research --from ideas.json --title "Why Lithography"   # substring
    yt research --title "How EUV light is made" --genre explainer \
        --description "Tin droplets, lasers and 13.5 nm light."
    cat one-idea.json | yt research -
    yt research --from ideas.json --index 1 --download --model opus

Every stage is cached under the dossier directory, so a re-run resumes instead
of paying twice; --fresh forces the work again.

Downloaded assets are untrusted bytes from the open web: they are written to
<dossier>/downloads/ and never opened or executed by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage
from . import assets as yt_assets
from . import workspace
from .core import Idea, _merge_usage, slugify, write_json

RESEARCHER_SYSTEM = (
    "You are a documentary researcher for a science and engineering YouTube "
    "channel. You care about primary sources: papers, datasets, standards, "
    "court filings, archives, first-hand accounts. You quantify where numbers "
    "exist, you name the source for every claim, and you say plainly when "
    "something is contested, unverified or just a popular misconception. You "
    "never invent a URL, a figure or a citation."
)

DEFAULT_DEPTH = 6

#: A genre, or a premise, that says the film speaks no claim aloud. The
#: research for such a piece is about what is on screen -- reference, look,
#: the physics of the motion -- not about defending figures nobody will hear.
_WORDLESS_GENRE = re.compile(
    r"cinematic|music video|ambient|\bmood\b|wordless|showreel|trailer|\bloop\b|"
    r"visualis|visualiz", re.I)
_WORDLESS_PREMISE = re.compile(
    r"\bno (?:narration|narrator|voice[- ]?over|dialogue|words|spoken)|wordless|"
    r"without (?:narration|a narrator|voice[- ]?over|words)|music[- ]only|"
    r"sound design only|music video|ambient (?:loop|film|piece|video)|captions only",
    re.I)

#: What each scope asks of the staged plan. The explainer scope is the one
#: every narrated video has always had; the cinematic scope is lighter and
#: aimed at the picture.
SCOPES = {
    "explainer": {
        "name": "explainer",
        "spoken": True,
        "misconceptions": True,
        "depth": DEFAULT_DEPTH,
        "plan": (
            "Plan the research for this video. Give exactly {depth} questions "
            "that must be answered before scripting -- the ones that decide "
            "whether the premise survives contact with the facts. Favour "
            "questions with checkable answers: numbers, dates, mechanisms, "
            "named decisions. Avoid vague framing questions. For each, give "
            "2-4 web searches that would actually find the answer, and restate "
            "the premise in one sentence."),
        "claims": (
            "Extract every factual claim worth putting on camera. Use only "
            "what the notes support -- do not add knowledge of your own. Keep "
            "each claim's exact number where there is one, attach the URL it "
            "came from, mark whether that URL is a primary or secondary "
            "source, and set confidence honestly. If a claim appears in the "
            "notes with no URL, mark it unsourced and low confidence rather "
            "than dropping it."),
        "shots": "",
        "gaps": (
            "Audit this research before scripting. List what is missing, "
            "weakly sourced, contested or likely to draw a correction in the "
            "comments, with how to resolve each. Note the misconceptions the "
            "video should address head-on. Then give a one-sentence verdict on "
            "whether the premise holds as stated."),
        "outline": (
            "Write the script outline. Open on the strongest concrete fact, "
            "not a definition. Each section gets a heading, a runtime in "
            "seconds, 2-5 beats in the order they are said, the claims it "
            "rests on (quote the claim text) and the assets it shows (quote "
            "their titles). Put the weakest material last or cut it. Total "
            "runtime should match the target length."),
    },
    "cinematic": {
        "name": "cinematic",
        "spoken": False,
        "misconceptions": False,
        "depth": 3,
        "plan": (
            "Plan the research for this film. It speaks no claim aloud -- no "
            "narration, no figures on screen -- so nothing here is fact-checked "
            "for a viewer. Give exactly {depth} questions, each about what is "
            "on screen: visual reference for the subject and its setting (how "
            "it really looks, is lit and is framed), the look the piece is "
            "after (films, photographers, materials, light), and the physics "
            "of what moves -- how the thing really behaves, so the animation "
            "reads true. No questions about records, markets, pricing, "
            "history or whether the premise is true. For each, give 2-4 web "
            "searches that would find pictures, footage or a description of "
            "the behaviour, and restate the premise in one sentence."),
        "claims": (
            "Extract only what the animation and the look depend on: how the "
            "subject behaves on screen (speeds, timings, the way it moves, "
            "settles or fails), what it and its setting look like, and the "
            "reference worth matching. A handful of claims is right; a film "
            "with no narration carries no figure to the viewer, so skip "
            "records, pricing, history and anything nobody will see. Use only "
            "what the notes support. Keep each claim's number where there is "
            "one, attach the URL it came from, mark primary or secondary, set "
            "confidence honestly, and mark the unsourced ones as such rather "
            "than dropping them."),
        "shots": (
            "These are reference for the authoring stage -- what the subject, "
            "its setting, its materials and its light really look like -- not "
            "footage to cut into the film."),
        "gaps": (
            "Audit this research before authoring. The film makes no spoken "
            "claim and puts no figure on screen, so unsourced numbers are not "
            "blockers: a blocker is only something that would make what is on "
            "screen read wrong -- a behaviour, a look or a material the "
            "reference does not support. List what is missing or weakly "
            "supported for the look and the motion, with how to resolve each "
            "(a reference image, a measured clip, or a stated assumption). "
            "Return an empty misconceptions list; there is nothing to correct "
            "out loud. Then give a one-sentence verdict on whether the piece "
            "can be made to read true."),
        "outline": (
            "Write the shot outline. There is no narration: sections are the "
            "beats of the piece in order, each with a heading, a runtime in "
            "seconds, 2-5 beats describing what is seen, the behaviours the "
            "motion rests on under claims_used (quote the claim text) and the "
            "reference it matches under assets_used (quote the titles). Total "
            "runtime should match the target length."),
    },
}


def scope_for(idea: Idea) -> dict:
    """Which research plan an idea gets, from its genre and premise.

    A wordless piece -- cinematic, a music video, an ambient loop, "no
    narration" -- gets the lighter cinematic scope; everything else keeps the
    explainer scope that narrated videos have always had.
    """
    if _WORDLESS_GENRE.search(idea.genre or "") or \
            _WORDLESS_PREMISE.search(idea.description or ""):
        return dict(SCOPES["cinematic"])
    return dict(SCOPES["explainer"])


#: A film this long (or longer, or of unknown length) is researched at the
#: full scale the scopes were written for; shorter ones shrink from it.
FULL_SCALE_SECONDS = 600
FULL_SHOTS = 14
MIN_SHOTS = 6
#: Claims worth keeping for a one-minute film, and what each further minute adds.
CLAIMS_FIRST_MINUTE = 40
CLAIMS_PER_MINUTE = 20

_LENGTH = re.compile(
    r"(?P<clock>\d+:\d{2})|"
    r"(?P<a>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<b>\d+(?:\.\d+)?))?\s*"
    r"(?P<unit>hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)


def runtime_seconds(length: str | None) -> int | None:
    """The target runtime an idea's free-text length names, or None.

    Reads "60 seconds", "90s", "8-12 min" (the middle), "10:00" and "1 hour".
    """
    m = _LENGTH.search(length or "")
    if not m:
        return None
    if m["clock"]:
        mins, secs = m["clock"].split(":")
        return int(mins) * 60 + int(secs)
    value = float(m["a"])
    if m["b"]:
        value = (value + float(m["b"])) / 2
    return round(value * {"h": 3600, "m": 60, "s": 1}[m["unit"].lower()[0]])


def runtime_plan(idea: Idea, scope_depth: int) -> dict:
    """Questions, shots and a claim ceiling for the idea's target runtime.

    Everything shrinks linearly from the full-scale values as the film gets
    shorter than ten minutes; ten minutes, more, or no stated length plans
    exactly as before (no claim ceiling, scope depth, 14 shots).
    """
    seconds = runtime_seconds(idea.length)
    if seconds is None or seconds >= FULL_SCALE_SECONDS:
        return {"depth": scope_depth, "shots": FULL_SHOTS, "claims": None}
    minutes = max(seconds, 60) / 60
    share = (minutes - 1) / (FULL_SCALE_SECONDS / 60 - 1)
    claims = CLAIMS_FIRST_MINUTE + CLAIMS_PER_MINUTE * (minutes - 1)
    return {
        "depth": max(2, min(scope_depth, 2 + round((scope_depth - 2) * share))),
        "shots": MIN_SHOTS + round((FULL_SHOTS - MIN_SHOTS) * share),
        "claims": round(claims / 5) * 5,
    }


COMMONS_API = "https://commons.wikimedia.org/w/api.php"

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024

#: Media hosts rate-limit hard (Wikimedia returns 429 after a couple of quick
#: hits), so requests to one host are spaced out and retried politely.
HOST_DELAY_SECONDS = 1.2
DOWNLOAD_ATTEMPTS = 2
DEFAULT_USER_AGENT = yt_assets.DEFAULT_USER_AGENT

#: After this many rate-limit refusals from one host, stop asking it. The
#: window is per-IP and longer than any backoff worth sitting through, so the
#: remaining files are marked for a later run instead of hammering the host.
RATE_LIMIT_TRIP = 2


#: Cross-run backoff. In-run retries cannot clear an IP-level rate limit, so
#: a refusal is remembered and doubles each consecutive time: 15m, 30m, 1h ...
COOLDOWN_BASE_SECONDS = 900
COOLDOWN_CAP_SECONDS = 6 * 3600
COOLDOWN_FILE = ".host-cooldown.json"


class RateLimited(Exception):
    """A host asked us to back off (HTTP 429/503)."""


class HostCooldown:
    """Remembers which hosts refused us, so a re-run waits instead of asking.

    The state is shared by every dossier under one --out directory, because a
    rate limit applies to this machine, not to one video's research.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: dict[str, dict] = {}
        if path.exists():
            try:
                self.state = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.state = {}

    def remaining(self, host: str) -> float:
        """Seconds still to wait before this host should be asked again."""
        entry = self.state.get(host)
        return max(0.0, entry["until"] - time.time()) if entry else 0.0

    def strike(self, host: str) -> float:
        entry = self.state.setdefault(host, {"strikes": 0, "until": 0.0})
        entry["strikes"] += 1
        wait = min(COOLDOWN_BASE_SECONDS * 2 ** (entry["strikes"] - 1),
                   COOLDOWN_CAP_SECONDS)
        entry["until"] = time.time() + wait
        return wait

    def clear(self, host: str) -> None:
        self.state.pop(host, None)

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.state, indent=2) + "\n",
                                 encoding="utf-8")
        except OSError:
            pass


def _human(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 60:.0f}m" if seconds >= 60 else f"{seconds:.0f}s"


class _HostThrottle:
    """Keeps consecutive requests to the same host at least `delay` apart."""

    def __init__(self, delay: float = HOST_DELAY_SECONDS) -> None:
        self.delay = delay
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            due = self._last.get(host, 0.0) + self.delay
            if due > now:
                time.sleep(due - now)
                now = time.monotonic()
            self._last[host] = now


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "premise": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "searches": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "why", "searches"],
            },
        },
    },
    "required": ["premise", "questions"],
}

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "detail": {"type": "string"},
                    "number": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_kind": {"type": "string",
                                    "enum": ["primary", "secondary", "unsourced"]},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                    "caveat": {"type": "string"},
                },
                "required": ["claim", "detail", "source_title", "source_url",
                             "source_kind", "confidence"],
            },
        }
    },
    "required": ["claims"],
}

SOURCES_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "publisher": {"type": "string"},
                    "year": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["paper", "dataset", "book", "archive",
                                      "standard", "news", "video", "interview",
                                      "encyclopedia", "blog", "other"]},
                    "access": {"type": "string",
                               "enum": ["open", "paywalled", "unknown"]},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["title", "url", "kind", "why_it_matters"],
            },
        }
    },
    "required": ["sources"],
}

SHOTS_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_searches": {"type": "array", "items": {"type": "string"}},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot": {"type": "string"},
                    "why": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["image", "video", "animation", "audio",
                                      "dataset", "map", "document", "3d-model",
                                      "article"]},
                    "searches": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["shot", "why", "kind", "searches"],
            },
        }
    },
    "required": ["topic_searches", "shots"],
}

VET_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "as": {"type": "string"},
                },
                "required": ["n", "as"],
            },
        }
    },
    "required": ["keep"],
}

ASSETS_SCHEMA = {
    "type": "object",
    "properties": {
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["image", "video", "audio", "animation",
                                      "dataset", "3d-model", "map", "document"]},
                    "url": {"type": "string"},
                    "page_url": {"type": "string"},
                    "source": {"type": "string"},
                    "licence": {"type": "string"},
                    "credit_line": {"type": "string"},
                    "commercial_ok": {"type": "boolean"},
                    "use_for": {"type": "string"},
                },
                "required": ["title", "kind", "url", "source", "licence",
                             "credit_line", "use_for"],
            },
        }
    },
    "required": ["assets"],
}

GAPS_SCHEMA = {
    "type": "object",
    "properties": {
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "severity": {"type": "string",
                                 "enum": ["blocker", "important", "minor"]},
                    "how_to_resolve": {"type": "string"},
                },
                "required": ["issue", "severity", "how_to_resolve"],
            },
        },
        "misconceptions": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
    },
    "required": ["gaps", "verdict"],
}

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "working_title": {"type": "string"},
        "runtime": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "seconds": {"type": "integer"},
                    "beats": {"type": "array", "items": {"type": "string"}},
                    "claims_used": {"type": "array", "items": {"type": "string"}},
                    "assets_used": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "seconds", "beats"],
            },
        },
    },
    "required": ["working_title", "runtime", "sections"],
}


# --------------------------------------------------------------------------- #
# dossier storage
# --------------------------------------------------------------------------- #

class Dossier:
    """A directory holding every stage's output, reusable across runs."""

    def __init__(self, root: Path, idea: Idea, *, at: Path | None = None) -> None:
        # `at` pins the directory exactly; otherwise it is <root>/<slug>, which
        # is what the workspace layout produces anyway.
        self.dir = at or (root / slugify(idea.title))
        self.raw = self.dir / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.idea = idea

    def _path(self, name: str) -> Path:
        return self.raw / f"{name}.json"

    def load(self, name: str) -> object | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save(self, name: str, data: object) -> None:
        write_json(self._path(name), data)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path


# --------------------------------------------------------------------------- #
# the researcher
# --------------------------------------------------------------------------- #

class Researcher:
    """Runs the staged research pipeline for one idea."""

    WEB_TOOLS = ("WebSearch", "WebFetch")

    def __init__(self, idea: Idea, dossier: Dossier, *, model: str = "sonnet",
                 depth: int | None = None, jobs: int = 3, budget: float | None = None,
                 fresh: bool = False, verbose: bool = True,
                 shot_count: int | None = None, per_shot: int = 2,
                 asset_sources: tuple[str, ...] = yt_assets.DEFAULT_SOURCES,
                 allow_sa: bool = False, agentic_assets: bool = False,
                 user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.idea = idea
        self.dossier = dossier
        self.model = model
        # The genre sets the plan; an explicit --depth still wins.
        self.scope = scope_for(idea)
        # The film's length scales it down; an explicit --depth/--shots wins.
        plan = runtime_plan(idea, self.scope["depth"])
        self.depth = depth or plan["depth"]
        self.claim_ceiling = plan["claims"]
        self.jobs = max(1, jobs)
        self.budget = budget
        self.fresh = fresh
        self.verbose = verbose
        self.shot_count = shot_count or plan["shots"]
        self.per_shot = per_shot
        self.asset_sources = asset_sources
        self.allow_sa = allow_sa
        self.agentic_assets = agentic_assets
        self.user_agent = user_agent
        self.usage = Usage()
        self.visited: list[str] = []
        #: Set once this run searched the catalogues again, so the outline
        #: that quotes the old assets is written again too.
        self.assets_searched = False

    # -- plumbing ----------------------------------------------------------

    def _config(self, *, web: bool) -> EngineConfig:
        return EngineConfig(
            tools=self.WEB_TOOLS if web else (),
            model=self.model,
            permission_mode="dontAsk",
            max_turns_budget_usd=self.budget,
            timeout=1800.0 if web else 900.0,
        )

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _idea_block(self) -> str:
        return self.idea.block()

    def _stage(self, name: str, work, *, redo: bool = False) -> object:
        """Run `work()` unless this stage is already on disk (or `redo`)."""
        if not (self.fresh or redo):
            cached = self.dossier.load(name)
            if cached is not None:
                self._say(f"[{name}] cached")
                return cached
        self._say(f"[{name}] running")
        data = work()
        self.dossier.save(name, data)
        return data

    def _ask_json(self, prompt: str, schema: dict) -> object:
        engine = ClaudeEngine(self._config(web=False))
        try:
            return engine.ask_json(prompt, schema, system=RESEARCHER_SYSTEM)
        finally:
            _merge_usage(self.usage, engine.usage)

    def _research_one(self, question: dict) -> dict:
        """One agentic web-research pass; records every URL actually fetched."""
        engine = ClaudeEngine(self._config(web=True))
        searches = ", ".join(question.get("searches", [])) or question["question"]
        prompt = (
            f"Research this question for a YouTube video.\n\n{self._idea_block()}\n\n"
            f"QUESTION: {question['question']}\nWHY IT MATTERS: {question.get('why', '')}\n"
            f"SUGGESTED SEARCHES: {searches}\n\n"
            "Use WebSearch and WebFetch. Prefer primary sources: papers, "
            "datasets, standards, official reports, archives. Open the pages "
            "rather than trusting search snippets.\n\n"
            "Write dense research notes: specific numbers with units and dates, "
            "named people and organisations, direct quotes worth reading on "
            "camera, and the full URL beside every fact. Flag anything you "
            "could not verify. No preamble, no summary of your process."
        )
        visited: list[str] = []
        notes: list[str] = []
        searches = 0
        denied: list[dict] = []
        try:
            for event in engine.stream(prompt):
                if event.subtype == "tool_use" and event.tool_input:
                    url = event.tool_input.get("url")
                    query = event.tool_input.get("query")
                    if url:
                        visited.append(url)
                        self._say(f"    fetch {url[:90]}")
                    elif query:
                        searches += 1
                        self._say(f"    search {query[:90]}")
                if event.type == "assistant" and event.text:
                    notes.append(event.text)
                if event.type == "result":
                    # A denied tool call reaches the model as an ordinary tool
                    # result, so it apologises and answers from memory with no
                    # error anywhere. The engine's denial list is the canary.
                    denied = _denials(event)
        finally:
            _merge_usage(self.usage, engine.usage)
        if denied:
            wanted = "; ".join(sorted({d["wanted"] for d in denied if d["wanted"]}))
            self._say(f"    WARNING: {', '.join(sorted({d['tool'] for d in denied}))} "
                      f"denied; these notes may be written from memory"
                      + (f" (wanted: {wanted[:120]})" if wanted else ""))
        elif not visited and not searches:
            self._say("    WARNING: no search or fetch happened; these notes may "
                      "be written from memory")
        return {"question": question["question"],
                "notes": "\n".join(notes).strip(),
                "visited": visited,
                "searches": searches,
                "denied": denied}

    # -- stages ------------------------------------------------------------

    def questions(self) -> dict:
        return self._stage("01-questions", lambda: self._ask_json(
            f"{self._idea_block()}\n\n"
            + self.scope["plan"].format(depth=self.depth),
            QUESTIONS_SCHEMA))

    def research(self, questions: list[dict]) -> list[dict]:
        def work() -> list[dict]:
            self._say(f"  {len(questions)} questions, {self.jobs} at a time")
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = [pool.submit(self._research_one, q) for q in questions]
                out = []
                for n, future in enumerate(futures, 1):
                    try:
                        out.append(future.result())
                    except ClaudeError as exc:
                        self._say(f"  question {n} failed: {exc}")
                return out
        notes = self._stage("02-research", work)
        for entry in notes:
            self.visited += entry.get("visited", [])
        return notes

    def _notes_block(self, notes: list[dict]) -> str:
        return "\n\n".join(
            f"### {e['question']}\n{e['notes']}\n\nURLs opened: "
            f"{', '.join(e.get('visited', [])) or 'none'}"
            for e in notes)

    def claims(self, notes: list[dict]) -> dict:
        return self._stage("03-claims", lambda: self._ask_json(
            f"{self._idea_block()}\n\nResearch notes:\n\n{self._notes_block(notes)}\n\n"
            + self.scope["claims"]
            + (f" The film is short: keep about {self.claim_ceiling} claims "
               "at most, the strongest ones." if self.claim_ceiling else ""),
            CLAIMS_SCHEMA))

    def sources(self, notes: list[dict]) -> dict:
        return self._stage("04-sources", lambda: self._ask_json(
            f"{self._idea_block()}\n\nResearch notes:\n\n{self._notes_block(notes)}\n\n"
            "Build the source list for this video's description and for "
            "fact-checking. One entry per distinct source actually present in "
            "the notes, deduplicated, best first. Include the publisher and "
            "year where the notes give them, and say in one clause what the "
            "video takes from each. Do not invent URLs.",
            SOURCES_SCHEMA))

    def shots(self, notes: list[dict]) -> dict:
        """What the video must show. The model plans; it never supplies URLs."""
        return self._stage("05-shots", lambda: self._ask_json(
            f"{self._idea_block()}\n\nResearch so far:\n\n"
            f"{self._notes_block(notes)}\n\n"
            f"Write the shot list: {self.shot_count} visuals this video needs, "
            "in rough running order. Each is one concrete thing the viewer "
            "sees, not an abstraction.\n\n"
            "Do NOT give URLs, file names or sources -- image catalogues are "
            "searched separately. Instead give 2-4 search queries per shot, "
            "each a short noun phrase of two to four words, the way a stock "
            "library is actually searched ('silicon wafer', 'tokamak "
            "assembly', 'tin droplet'). Long descriptive sentences find "
            "nothing. Prefer subjects that public archives really hold: "
            "hardware, facilities, people at work, instruments, satellite and "
            "microscope imagery, historical photographs, archival footage and "
            "sound recordings, figures and supplementary movies from "
            "open-access papers, published 3D structures and models, and news "
            "coverage of the subject. Each query is sent to image, footage, "
            "sound, paper, structure and news catalogues alike, so name the "
            "thing itself; set `kind` to the medium the shot needs most.\n\n"
            "Also give `topic_searches`: 2-4 queries naming the subject itself "
            "at its most searchable ('EUV lithography', 'photolithography', "
            "'ASML'). These are tried for every shot, because an archive's "
            "best material on a subject is usually filed under the subject's "
            "plain name rather than under a specific scene."
            + (f"\n\n{self.scope['shots']}" if self.scope["shots"] else ""),
            SHOTS_SCHEMA))

    #: Raised when the asset search changes what it can find, so a dossier an
    #: older search filled is searched again instead of replayed.
    ASSET_SEARCH = 2

    def _asset_search_key(self) -> dict:
        return {"version": self.ASSET_SEARCH, "sources": list(self.asset_sources),
                "allow_sa": self.allow_sa, "per_shot": self.per_shot,
                "agentic": self.agentic_assets}

    def assets(self, notes: list[dict], sources: dict | None = None) -> dict:
        """Fill the shot list from the catalogues, verified live, and add the
        research's own open-access citations.

        The cached search is replayed only when it was made the same way:
        one made with other catalogues, another licence policy or an older
        searcher never looked for what this one would find.
        """
        plan = self.shots(notes)
        key = self._asset_search_key()
        cached = None if self.fresh else self.dossier.load("05-assets")
        if isinstance(cached, dict) and cached.get("search") == key:
            self._say("[05-assets] cached")
            yt_assets.settle_kinds(cached.get("assets", []))
            return cached
        self._say("[05-assets] running" + (
            " (the cached search was made another way)" if cached else ""))
        found = yt_assets.find_assets(
            plan.get("shots", []),
            topic_searches=plan.get("topic_searches", []),
            sources=self.asset_sources,
            per_need=self.per_shot,
            shortlist=self.per_shot * 3,
            allow_sa=self.allow_sa,
            user_agent=self.user_agent,
            verbose=self.verbose,
        )
        if "papers" in self.asset_sources:
            found += yt_assets.cited_papers(
                [s.get("url", "") for s in (sources or {}).get("sources", [])],
                allow_sa=self.allow_sa, user_agent=self.user_agent,
                verbose=self.verbose)
        found = self._vet_assets(found)
        if self.agentic_assets:
            found += self._agentic_assets(notes, found)
        data = {"assets": found, "search": key}
        self.dossier.save("05-assets", data)
        self.assets_searched = True
        return data

    def _vet_assets(self, candidates: list[dict]) -> list[dict]:
        """Keep only candidates that actually show the shot they were found for.

        Keyword search cannot tell a Zeiss multilayer mirror from a pair of
        Zeiss binoculars, so the model judges the titles the catalogues
        returned. It only ever selects from real, verified URLs.
        """
        if not candidates:
            return []
        listing = "\n".join(
            f"{n}. [{c['kind']}] {c['title']} — {c['source']}, {c['licence']}"
            f"{', ' + c['resolution'] if c.get('resolution') else ''}"
            f"{' (' + c['note'][:90] + ')' if c.get('note') and c['kind'] in ('structure', 'audio', 'video', 'article') else ''}"
            f"\n     wanted for: {c.get('use_for', '')}"
            for n, c in enumerate(candidates, 1))
        verdict = self._ask_json(
            f"{self._idea_block()}\n\n"
            "These files came back from open media catalogues -- stills, "
            "footage, sound, open-access papers with their figures and "
            "movies, 3D structures and models, and news coverage -- for the "
            f"shots listed under each one:\n\n{listing}\n\n"
            "Keep only the ones that genuinely serve what the shot needs. "
            "Keyword search returns look-alikes -- a photo of Zeiss binoculars "
            "for a Zeiss mirror, a vodka bottle for an industrial laser, a "
            "galaxy for a transmission graph. Reject those. Judge each by its "
            "kind: a structure or 3D model is geometry a 3D author builds "
            "from, kept when it is the very thing the shot shows; a paper or "
            "its figure is kept when the film rests on it or can show it; a "
            "sound when it could be heard under the shot; a news article is a "
            "reference the film may cite by outlet and headline, kept when it "
            "reports what the shot is about. A loose but honest stand-in is "
            "acceptable only if you say so in `as`. Keep at most "
            f"{self.per_shot} of each kind per shot -- the papers the research "
            "cites count as one shot -- and keep none rather than something "
            "misleading.",
            VET_SCHEMA)
        keep = {int(r["n"]): r for r in verdict.get("keep", [])
                if 1 <= int(r.get("n", 0)) <= len(candidates)}
        out, taken = [], set()
        for n, candidate in enumerate(candidates, 1):
            if n not in keep or candidate["url"] in taken:
                continue
            taken.add(candidate["url"])
            row = dict(candidate)
            row["use_for"] = keep[n].get("as") or row.get("use_for", "")
            out.append(row)
        self._say(f"  vetted: kept {len(out)} of {len(candidates)} candidates")
        return out

    def _agentic_assets(self, notes: list[dict], found: list[dict]) -> list[dict]:
        """Optional top-up for things no open catalogue holds (press kits etc.)."""
        engine = ClaudeEngine(self._config(web=True))
        have = "\n".join(f"- {a['title']}" for a in found) or "nothing yet"
        prompt = (
            f"{self._idea_block()}\n\nResearch so far:\n\n"
            f"{self._notes_block(notes)}\n\n"
            f"Open image catalogues already supplied these visuals:\n{have}\n\n"
            "Find what they cannot: company press-kit imagery, institutional "
            "media libraries, open-access paper figures, government archives. "
            "Open each page and read the real licence. Report title, kind, the "
            "direct file URL, the page it lives on, the source, the exact "
            "licence string, the on-screen credit, whether commercial use is "
            "allowed, and which beat it serves. Never guess a URL or a licence; "
            "report fewer assets rather than invented ones."
        )
        try:
            answer = engine.agent(prompt)
        finally:
            _merge_usage(self.usage, engine.usage)
        extra = self._ask_json(
            "Convert these findings into structured records, keeping URLs and "
            f"licence strings exactly as written. Drop anything with no URL.\n\n"
            f"{answer.text}", ASSETS_SCHEMA).get("assets", [])
        # Hold these to the same standard: real licence, real live file.
        checked = verify_licences(extra, user_agent=self.user_agent,
                                  verbose=self.verbose)
        out = []
        for row in checked:
            hit = yt_assets.verify_hit(
                yt_assets.AssetHit(title=row.get("title", ""), url=row.get("url", ""),
                                   source=row.get("source", "web"),
                                   licence=row.get("licence_verified")
                                   or row.get("licence", "")),
                self.user_agent)
            row["licence_check"] = (row.get("licence_check") or "") + \
                ("; file verified" if hit.verified else f"; unverified ({hit.note})")
            row["verified"] = hit.verified
            out.append(row)
        return out

    def gaps(self, claims: dict, sources: dict) -> dict:
        return self._stage("06-gaps", lambda: self._ask_json(
            f"{self._idea_block()}\n\n"
            f"Claims:\n{json.dumps(claims.get('claims', []), indent=2)}\n\n"
            f"Sources:\n{json.dumps(sources.get('sources', []), indent=2)}\n\n"
            + self.scope["gaps"],
            GAPS_SCHEMA))

    def outline(self, claims: dict, assets: dict, gaps: dict) -> dict:
        # The outline quotes the assets each section shows; once the
        # catalogues were searched again, the cached one names the old set.
        used = sorted(a.get("url", "") for a in assets.get("assets", []))
        redo = self.assets_searched or \
            self.dossier.load("07-outline-assets") not in (None, used)
        outline = self._stage("07-outline", lambda: self._ask_json(
            f"{self._idea_block()}\n\n"
            f"Claims:\n{json.dumps(claims.get('claims', []), indent=2)}\n\n"
            f"Assets:\n{json.dumps(assets.get('assets', []), indent=2)}\n\n"
            f"Audit:\n{json.dumps(gaps, indent=2)}\n\n"
            + self.scope["outline"],
            OUTLINE_SCHEMA), redo=redo)
        self.dossier.save("07-outline-assets", used)
        return outline

    # -- run ---------------------------------------------------------------

    def run(self) -> dict:
        plan = self.questions()
        notes = self.research(plan.get("questions", []))
        claims = self.claims(notes)
        sources = self.sources(notes)
        assets = self.assets(notes, sources)
        gaps = self.gaps(claims, sources)
        outline = self.outline(claims, assets, gaps)
        return {
            "idea": asdict(self.idea),
            "scope": self.scope["name"],
            "premise": plan.get("premise", ""),
            "questions": plan.get("questions", []),
            "shots": (self.dossier.load("05-shots") or {}).get("shots", []),
            "notes": notes,
            "claims": claims.get("claims", []),
            "sources": sources.get("sources", []),
            "assets": assets.get("assets", []),
            "gaps": gaps.get("gaps", []),
            "misconceptions": gaps.get("misconceptions", []),
            "verdict": gaps.get("verdict", ""),
            "outline": outline,
            "urls_opened": sorted(set(self.visited)),
            "warnings": provenance_warnings(notes),
            "allow_sa": self.allow_sa,
            "usage": asdict(self.usage),
        }


def _denials(event) -> list[dict]:
    """Denied tool calls on a result event, one per distinct attempt.

    The model retries a refused search once or twice, so the raw list
    overstates what was lost; entries are folded by tool and query. Prefers
    the engine's own field and falls back to the payload for older engines.
    """
    rows = getattr(event, "permission_denials", None)
    if rows is None:
        rows = (event.raw or {}).get("permission_denials") or []
    seen, out = set(), []
    for row in rows:
        if not isinstance(row, dict):
            row = {"tool_name": str(row)}
        tool = str(row.get("tool_name") or "?")
        args = row.get("tool_input") or {}
        wanted = str(args.get("query") or args.get("url") or "")
        if (tool, wanted) in seen:
            continue
        seen.add((tool, wanted))
        out.append({"tool": tool, "wanted": wanted})
    return out


def provenance_warnings(notes: list[dict]) -> list[str]:
    """What, if anything, says these notes did not come from the web.

    Written into the dossier rather than only printed, so a later stage -- or
    `yt doctor` -- can see that a question was answered blind.
    """
    out = []
    for n, entry in enumerate(notes, 1):
        if entry.get("denied"):
            tools = sorted({str(d.get("tool", d)) if isinstance(d, dict) else str(d)
                            for d in entry["denied"]})
            how = ("searched but could not open pages" if tools == ["WebFetch"]
                   else "could not search")
            out.append(f"question {n}: {', '.join(tools)} denied, so it {how}; "
                       "its notes may be written from memory")
        elif not entry.get("visited") and not entry.get("searches"):
            out.append(f"question {n}: no search or fetch happened; its notes "
                       "may be written from memory")
        elif not entry.get("visited"):
            out.append(f"question {n}: searched but opened no page; its facts "
                       "rest on search snippets")
    return out


# --------------------------------------------------------------------------- #
# downloading assets
# --------------------------------------------------------------------------- #

def _filename(url: str, index: int, content_type: str | None) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", Path(urlparse(url).path).name)
    suffix = Path(base).suffix
    # Shorten the stem, never the extension: a cut one made a file's type
    # unreadable (".ogv" became a guessed ".ogx").
    if len(base) > 80:
        keep = suffix if 1 < len(suffix) <= 6 else ""
        base = base[:80 - len(keep)] + keep
    suffix = Path(base).suffix
    # ".07283" from an arXiv id is not a file extension; a real one is alphabetic.
    if not suffix or not suffix[1:].isalpha():
        ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
        base = f"{base or 'asset'}{ext or '.bin'}"
    return f"{index:02d}-{base}"


#: Landing pages whose real file is one predictable hop away.
def direct_file_url(url: str) -> str:
    """Rewrite an obvious landing page to the file it is about."""
    arxiv = re.match(r"(https?://arxiv\.org)/abs/(.+)$", url)
    if arxiv:
        return f"{arxiv.group(1)}/pdf/{arxiv.group(2)}"
    return url


def _commons_filename(url: str) -> str | None:
    """The File: name behind a Wikimedia URL, direct or thumbnail."""
    match = re.search(
        r"upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?"
        r"[0-9a-f]/[0-9a-f]{2}/([^/?]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"wikimedia\.org/wiki/(?:File|Special:FilePath)[:/]([^?&#]+)", url)
    return match.group(1) if match else None


def verify_licences(assets: list[dict], *, user_agent: str = DEFAULT_USER_AGENT,
                    verbose: bool = True) -> list[dict]:
    """Replace claimed licences with Wikimedia's own metadata where we can.

    The Commons API lives on a different host from the files, so this works
    even when the file host is rate-limiting us. Attribution has to be exact,
    so an authoritative string beats a plausible one.
    """
    strip = lambda html: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()
    throttle = _HostThrottle(0.5)
    out: list[dict] = []

    for asset in assets:
        record = dict(asset)
        name = _commons_filename(asset.get("url", "")) or \
            _commons_filename(asset.get("page_url", "") or "")
        if not name:
            out.append(record)
            continue
        # Names arrive percent-encoded from upload URLs and raw from page
        # URLs; the API wants one consistent spelling, with underscores.
        title = urllib.parse.quote(urllib.parse.unquote(name).replace(" ", "_"))
        query = (f"{COMMONS_API}?action=query&format=json&prop=imageinfo"
                 f"&iiprop=url|extmetadata&titles=File:{title}")
        try:
            throttle.wait(query)
            request = urllib.request.Request(query, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=30) as response:
                pages = json.loads(response.read())["query"]["pages"]
            info = list(pages.values())[0]
        except (urllib.error.URLError, OSError, KeyError, ValueError,
                json.JSONDecodeError) as exc:
            record["licence_check"] = f"lookup failed: {type(exc).__name__}"
            out.append(record)
            continue

        if "imageinfo" not in info:
            record["licence_check"] = "not found on Commons -- check the URL"
            if verbose:
                print(f"    ? {name[:60]} not found on Commons", file=sys.stderr)
            out.append(record)
            continue

        image = info["imageinfo"][0]
        meta = image.get("extmetadata", {})
        licence = strip(meta.get("LicenseShortName", {}).get("value", ""))
        artist = strip(meta.get("Artist", {}).get("value", ""))
        if licence:
            record["licence_verified"] = licence
            record["licence_check"] = "verified against Commons"
        if artist:
            record["credit_line"] = f"{artist} / {licence} (Wikimedia Commons)"
        record["page_url"] = image.get("descriptionurl") or record.get("page_url", "")
        if verbose:
            print(f"    ✓ {name[:48]:50} {licence}", file=sys.stderr)
        out.append(record)
    return out


def is_free(asset: dict, *, allow_sa: bool = False) -> tuple[bool, str]:
    """Whether we may fetch and reuse this asset, and the licence we judged on.

    The policy itself lives in `assets.licence_verdict`, so the download gate
    and the catalogue search cannot disagree. A verified licence outranks a
    claimed one; the model's own `commercial_ok` flag only breaks a tie the
    string itself cannot settle.
    """
    licence = asset.get("licence_verified") or asset.get("licence", "")
    verdict, _ = yt_assets.licence_verdict(licence, allow_sa=allow_sa)
    if verdict == "free":
        return True, licence
    if verdict == "vetoed":
        return False, licence
    return bool(asset.get("commercial_ok")), licence


def _fetch(url: str, throttle: _HostThrottle, user_agent: str
           ) -> tuple[bytes, str]:
    """GET a URL, spacing requests per host and backing off on 429/503."""
    last: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        throttle.wait(url)
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return (response.read(MAX_DOWNLOAD_BYTES + 1),
                        response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 503):
                raise
            if attempt == DOWNLOAD_ATTEMPTS:
                raise RateLimited(str(exc.reason)[:120]) from exc
            # Honour Retry-After when the host sends one, else back off.
            retry_after = (exc.headers or {}).get("Retry-After", "")
            try:
                pause = min(float(retry_after), 30.0)
            except ValueError:
                pause = HOST_DELAY_SECONDS * (3 ** attempt)
            time.sleep(pause)
    raise last or urllib.error.URLError("download failed")


def download_assets(dossier: Dossier, assets: list[dict], *, verbose: bool = True,
                    user_agent: str = DEFAULT_USER_AGENT, force: bool = False,
                    allow_sa: bool = False) -> list[dict]:
    """Fetch freely licensed assets into <dossier>/downloads/ as inert files.

    A re-run fetches only what is missing: anything the last manifest recorded
    as downloaded, and whose file is still on disk, is carried forward
    untouched unless `force` is set.
    """
    out_dir = dossier.dir / "downloads"
    out_dir.mkdir(exist_ok=True)
    throttle = _HostThrottle()
    cooldown = HostCooldown(dossier.dir.parent / COOLDOWN_FILE)
    manifest: list[dict] = []
    refused: dict[str, int] = {}
    already = {row["url"]: row for row in (dossier.load("08-downloads") or [])
               if row.get("status") == "ok" and row.get("url")}

    for n, asset in enumerate((a for a in assets if a.get("url")), 1):
        url = asset["url"]
        licence = asset.get("licence_verified") or asset.get("licence", "")
        row = {"title": asset.get("title", ""), "url": url, "licence": licence,
               "credit_line": asset.get("credit_line", "")}
        have = already.get(url)
        if have and not force and (dossier.dir / have.get("file", "")).is_file():
            manifest.append(have | {"credit_line": row["credit_line"]})
            if verbose:
                print(f"    = {have['file'].split('/')[-1]} (already have)",
                      file=sys.stderr)
            continue
        if not url.lower().startswith(("http://", "https://")):
            manifest.append(row | {"status": "skipped: not http"})
            continue
        free, judged = is_free(asset, allow_sa=allow_sa)
        if not free:
            manifest.append(row | {"status": f"skipped: licence {judged!r}"})
            continue
        host = urlparse(direct_file_url(url)).netloc
        waiting = cooldown.remaining(host)
        if waiting:
            manifest.append(row | {"status": f"deferred: {host} cooling down, "
                                             f"retry in {_human(waiting)}"})
            continue
        if refused.get(host, 0) >= RATE_LIMIT_TRIP:
            manifest.append(row | {"status": f"deferred: {host} is rate-limiting, "
                                             "re-run --download later"})
            continue
        try:
            body, content_type = _fetch(direct_file_url(url), throttle, user_agent)
        except RateLimited as exc:
            refused[host] = refused.get(host, 0) + 1
            wait = cooldown.strike(host)
            manifest.append(row | {"status": f"deferred: rate-limited, retry in "
                                             f"{_human(wait)} ({exc})"})
            if verbose:
                print(f"    ~ {host} is rate-limiting; backing off {_human(wait)}"
                      + (", skipping its remaining files"
                         if refused[host] >= RATE_LIMIT_TRIP else ""), file=sys.stderr)
            continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            manifest.append(row | {"status": f"failed: {exc}"})
            if verbose:
                print(f"    x {url[:70]} ({exc})", file=sys.stderr)
            continue
        if len(body) > MAX_DOWNLOAD_BYTES:
            manifest.append(row | {"status": "skipped: larger than 25 MB"})
            continue
        if "text/html" in content_type.lower():
            # A landing page, not the asset. Keep the URL, drop the bytes.
            manifest.append(row | {"status": "skipped: that URL serves a web "
                                             "page, not a media file"})
            if verbose:
                print(f"    ! {url[:70]} is a web page, not a file", file=sys.stderr)
            continue

        cooldown.clear(host)
        path = out_dir / _filename(direct_file_url(url), n, content_type)
        path.write_bytes(body)
        got = {"status": "ok", "file": f"downloads/{path.name}",
               "bytes": len(body), "content_type": content_type}
        if path.suffix.lower() == ".stl" or "application/sla" in content_type.lower():
            # The engine imports OBJ, not STL: offer the converted mesh.
            mesh = yt_assets.stl_to_obj(body)
            if mesh:
                obj = path.with_suffix(".obj")
                obj.write_text(mesh, encoding="utf-8")
                got |= {"file": f"downloads/{obj.name}",
                        "source_file": f"downloads/{path.name}"}
        manifest.append(row | got)
        if verbose:
            print(f"    + {Path(got['file']).name} ({len(body) // 1024} KB)",
                  file=sys.stderr)

    cooldown.save()
    dossier.save("08-downloads", manifest)
    return manifest


def attach_downloads(assets: list[dict], downloads: list[dict]) -> list[dict]:
    """Give each downloaded asset its `file`, relative to the project.

    The authors' briefs offer an asset only when it names a file; the
    manifest knew the file, the dossier's assets never did, so nothing the
    research downloaded ever reached an author.
    """
    got = {d["url"]: d for d in downloads
           if d.get("status") == "ok" and d.get("file") and d.get("url")}
    for asset in assets:
        row = got.get(asset.get("url"))
        if row:
            asset["file"] = row["file"]
            if row.get("source_file"):
                asset["source_file"] = row["source_file"]
    return assets


# --------------------------------------------------------------------------- #
# written output
# --------------------------------------------------------------------------- #

def _table(rows: list[list[str]], headers: list[str]) -> list[str]:
    clean = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(clean(c) for c in row) + " |" for row in rows]
    return out


def write_dossier(dossier: Dossier, data: dict, downloads: list[dict]) -> list[Path]:
    idea = dossier.idea
    md: list[str] = [
        f"# {idea.title}", "",
        f"`{idea.genre}`" + (f" · {idea.length}" if idea.length else ""), "",
        f"**Premise.** {data['premise'] or idea.description}", "",
    ]
    if data.get("verdict"):
        md += [f"**Does it hold up?** {data['verdict']}", ""]
    if data.get("scope") == "cinematic":
        md += ["> **Scope.** A wordless piece: the research covers reference, the "
               "look and the physics of what is on screen. It speaks no claim, so an "
               "unsourced number is not a blocker here.", ""]
    if data.get("warnings"):
        md += ["> **Provenance.** " + " ".join(data["warnings"]) + " Treat the "
               "affected claims as unverified until re-run with `--fresh`.", ""]

    md += ["## Research questions", ""]
    md += [f"{n}. **{q['question']}** — {q.get('why', '')}"
           for n, q in enumerate(data["questions"], 1)]

    md += ["", "## Claims", ""]
    for claim in data["claims"]:
        mark = {"high": "●", "medium": "◐", "low": "○"}.get(claim.get("confidence"), "·")
        number = f" **{claim['number']}**" if claim.get("number") else ""
        md += [f"- {mark}{number} {claim['claim']}",
               f"  - {claim.get('detail', '')}",
               f"  - [{claim.get('source_title', 'source')}]({claim['source_url']})"
               f" ({claim.get('source_kind', '?')}, {claim.get('confidence', '?')}"
               f" confidence)"]
        if claim.get("caveat"):
            md += [f"  - ⚠ {claim['caveat']}"]
    md += ["", "`●` high · `◐` medium · `○` low confidence", ""]

    md += ["## Sources", ""]
    md += _table([[f"[{s['title']}]({s['url']})", s.get("kind", ""),
                   s.get("publisher", ""), s.get("year", ""),
                   s.get("access", ""), s.get("why_it_matters", "")]
                  for s in data["sources"]],
                 ["source", "kind", "publisher", "year", "access", "used for"])

    md += ["", "## Multimedia assets", ""]
    md += _table([[f"[{a['title']}]({a.get('page_url') or a['url']})",
                   a.get("kind", ""), a.get("source", ""),
                   (a.get("licence_verified") or a.get("licence", ""))
                   + (" ✓" if a.get("licence_verified") else ""),
                   "yes" if is_free(a, allow_sa=data.get("allow_sa", False))[0]
                   else "no",
                   a.get("use_for", "")]
                  for a in data["assets"]],
                 ["asset", "kind", "source", "licence", "usable", "use for"])
    md += ["", "`✓` licence verified against the host's own metadata; "
           "`usable` means free for a monetised video (NC and ND excluded).", ""]
    md += ["", "Credit lines to put on screen:", ""]
    md += [f"- {a['title']}: {a.get('credit_line', '')}" for a in data["assets"]]

    share_alike = [a for a in data["assets"]
                   if re.search(r"\bsa\b|share[- ]?alike",
                                a.get("licence_verified") or a.get("licence", ""), re.I)]
    if share_alike:
        md += ["", f"> **ShareAlike ({len(share_alike)} assets).** These carry a "
               "CC BY-SA licence: credit is not enough on its own, the licence "
               "also reaches anything built from them. Check how that applies to "
               "your cut before using them, or swap in a public-domain "
               "equivalent.", ""]
        md += [f"- {a['title']} — {a.get('licence_verified') or a['licence']}"
               for a in share_alike]

    if downloads:
        got = [d for d in downloads if d.get("status") == "ok"]
        md += ["", f"### Downloaded ({len(got)}/{len(downloads)})", ""]
        md += [f"- [{d.get('title') or d['file']}]({d['file']}) — {d['licence']}"
               for d in got]
        deferred = [d for d in downloads if d.get("status", "").startswith("deferred")]
        if deferred:
            md += ["", f"Rate-limited, re-run `--download` to retry ({len(deferred)}):", ""]
            md += [f"- [{d.get('title') or d['url']}]({d['url']}) — {d['licence']}"
                   for d in deferred]
        other = [d for d in downloads
                 if d.get("status") != "ok" and d not in deferred]
        if other:
            md += ["", "Not downloaded (fetch by hand if needed):", ""]
            md += [f"- {d.get('title') or d['url']} — {d['status']}" for d in other]

    if data["gaps"]:
        md += ["", "## Gaps and risks", ""]
        for gap in sorted(data["gaps"],
                          key=lambda g: ["blocker", "important", "minor"].index(
                              g.get("severity", "minor"))):
            md += [f"- **{gap.get('severity', '?')}** — {gap['issue']}",
                   f"  - fix: {gap.get('how_to_resolve', '')}"]
    if data.get("misconceptions"):
        md += ["", "## Misconceptions to address", ""]
        md += [f"- {m}" for m in data["misconceptions"]]

    outline = data.get("outline") or {}
    if outline.get("sections"):
        md += ["", "## Script outline", "",
               f"**{outline.get('working_title', idea.title)}** · "
               f"{outline.get('runtime', '')}", ""]
        for n, section in enumerate(outline["sections"], 1):
            md += [f"### {n}. {section['heading']} ({section.get('seconds', 0)}s)"]
            md += [f"- {beat}" for beat in section.get("beats", [])]
            if section.get("claims_used"):
                md += ["- *claims:* " + "; ".join(section["claims_used"])]
            if section.get("assets_used"):
                md += ["- *visuals:* " + "; ".join(section["assets_used"])]
            md.append("")

    md += ["", "## Provenance", "",
           f"- {len(data['urls_opened'])} URLs opened during research "
           "(see `raw/02-research.json`)",
           f"- cost: {Usage(**data['usage'])}", ""]

    written = [dossier.write("README.md", "\n".join(md)),
               write_json(dossier.dir / "dossier.json", data)]

    for name, rows in (("sources.csv", data["sources"]),
                       ("claims.csv", data["claims"]),
                       ("assets.csv", data["assets"])):
        if not rows:
            continue
        cols = sorted({k for row in rows for k in row})
        path = dossier.dir / name
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows({c: row.get(c, "") for c in cols} for row in rows)
        written.append(path)

    notes_md = ["# Raw research notes", ""]
    for entry in data["notes"]:
        opened = ", ".join(entry.get("visited", [])) or "none"
        notes_md += [f"## {entry['question']}", "", entry["notes"], "",
                     f"**URLs opened:** {opened}", ""]
    written.append(dossier.write("notes.md", "\n".join(notes_md)))
    return written


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "research", parents=list(parents),
        help="research one idea into a production dossier",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--depth", type=int, default=None,
                   help=f"research questions to pursue (default {DEFAULT_DEPTH}, or "
                        f"{SCOPES['cinematic']['depth']} for a wordless or "
                        "cinematic piece; fewer for a film under ten minutes)")
    p.add_argument("--jobs", type=int, default=3,
                   help="questions researched in parallel (default 3)")
    p.add_argument("--budget", type=float, metavar="USD",
                   help="hard spend cap per engine call")
    p.add_argument("--download", action="store_true",
                   help="fetch freely licensed assets into the project")
    p.add_argument("--shots", type=int, default=None, metavar="N",
                   help="visuals to plan for (default 14 for a ten-minute film, "
                        "fewer for a shorter one)")
    p.add_argument("--per-shot", type=int, default=2, metavar="N",
                   help="assets to find per shot (default 2)")
    p.add_argument("--asset-sources", default=",".join(yt_assets.DEFAULT_SOURCES),
                   help=f"catalogues to search: {', '.join(yt_assets.SOURCES)}")
    p.add_argument("--allow-sa", action="store_true",
                   help="also accept CC BY-SA assets (share-alike duties)")
    p.add_argument("--agentic-assets", action="store_true",
                   help="also let the model web-search for press-kit and "
                        "institutional imagery the catalogues lack")
    p.add_argument("--redownload", action="store_true",
                   help="re-fetch assets already on disk instead of only the "
                        "missing ones")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                   help="User-Agent for downloads; some media hosts ask for a "
                        "contact address on heavy use")
    p.add_argument("--fresh", action="store_true",
                   help="redo every stage instead of reusing cached ones")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args)
    dossier = Dossier(project.root, idea, at=project.root)
    verbose = not args.quiet
    if verbose:
        print(f"researching: {idea.title}\n  -> {dossier.dir}", file=sys.stderr)

    researcher = Researcher(
        idea, dossier, model=args.model, depth=args.depth, jobs=args.jobs,
        budget=args.budget, fresh=args.fresh, verbose=verbose,
        shot_count=args.shots, per_shot=args.per_shot,
        asset_sources=tuple(s for s in args.asset_sources.split(",") if s),
        allow_sa=args.allow_sa, agentic_assets=args.agentic_assets,
        user_agent=args.user_agent)
    try:
        data = researcher.run()
    except ClaudeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    downloads: list[dict] = dossier.load("08-downloads") or []
    if args.download:
        if verbose:
            print("[downloads] fetching", file=sys.stderr)
        downloads = download_assets(dossier, data["assets"], verbose=verbose,
                                    user_agent=args.user_agent,
                                    force=args.redownload,
                                    allow_sa=args.allow_sa)

    attach_downloads(data["assets"], downloads)
    written = write_dossier(dossier, data, downloads)
    print(f"\n{dossier.dir}/")
    for path in written:
        print(f"  {path.name}")
    print(f"\n{len(data['claims'])} claims · {len(data['sources'])} sources · "
          f"{len(data['assets'])} assets · {len(data['gaps'])} gaps")
    print(f"{researcher.usage}", file=sys.stderr)
    return 0
