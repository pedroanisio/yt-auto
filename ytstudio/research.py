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
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from claude_engine import ClaudeEngine, ClaudeError, EngineConfig, Usage
from . import assets as yt_assets
from . import flags, harness, spend, workspace
from .core import (PAPER_ARXIV, PAPER_DOI, PAPER_HTTP, Idea, _merge_usage,
                   paper_is_local, slugify, write_json)

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
            "named decisions. Avoid vague framing questions. One question asks "
            "what the subject costs or changes in the world and for whom, with "
            "the source that documents it: a significance question with a "
            "documented answer is not a vague framing question. Among them, ask "
            "what a lay viewer most often wrongly believes about the subject "
            "and where that belief is documented, and look for one named, "
            "identifiable case -- a person, a place, an event -- the film can "
            "follow. For each, give 2-4 web searches that would actually find "
            "the answer, and restate the premise in one sentence."),
        "claims": (
            "Extract every factual claim worth putting on camera. Use only "
            "what the notes support -- do not add knowledge of your own. Keep "
            "each claim's exact number where there is one, attach the URL it "
            "came from, mark whether that URL is a primary or secondary "
            "source, and set confidence honestly. If a claim appears in the "
            "notes with no URL, mark it unsourced and low confidence rather "
            "than dropping it. Give each claim its standing from how the source "
            "itself presents it, not from how reliable the source is: "
            "established for a result it reports as shown, hypothesis for a "
            "proposal or interpretation, speculation for a guess, and "
            "limitation for a limitation, open question, preliminary result or "
            "future work it names."),
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
            "runtime should match the target length. Also give "
            "misconceptions: each belief the film should break, with the "
            "source that documents people holding it (a survey, a study, a "
            "published correction); leave the list empty rather than name a "
            "belief no source documents. And identifiable_case: one named "
            "person, place or event the film can follow, with its source, or "
            "an empty who when the research found none."),
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
            "runtime should match the target length. Return an empty "
            "misconceptions list and an empty identifiable_case: nothing is "
            "said aloud to correct or to follow."),
    },
}


#: The paper scope (AUTO-131): the film explains one paper, read first in
#: its own pass (`00-paper`). Its plan asks what a film about a paper needs
#: beyond the mechanism; the claims cite the paper's own URL for what it
#: reports (`{paper_url}`, filled by scope_for); the gap audit is the
#: explainer's; the outline puts the problem and why it matters before the
#: mechanism and is handed the paper's digest (`06b-paper`).
SCOPES["paper"] = {
    "name": "paper",
    "spoken": True,
    "misconceptions": True,
    "depth": DEFAULT_DEPTH,
    "plan": (
        "Plan the research for this video, whose subject is one paper (the "
        "Paper above, with what it says as read). Give exactly {depth} "
        "questions that must be answered before scripting, each answerable "
        "from a source the film can cite. Among them ask, with numbers where "
        "there are any (fold two into one question when there are fewer "
        "questions than these): the problem the paper addresses and what it "
        "costs in the world, and for whom; the state of the art before it and "
        "what the paper adds; how independent researchers, reviews, "
        "replications and the press received it; the documented downstream "
        "consequences and applications; and the paper's limitations, its open "
        "questions and what would refute it. The remaining questions stay "
        "checkable mechanism questions: numbers, dates, how the method works, "
        "named decisions. For each, give 2-4 web searches that would actually "
        "find the answer, and restate the premise in one sentence."),
    "claims": SCOPES["explainer"]["claims"] + (
        " What the paper itself reports is cited to the paper's own URL "
        "({paper_url}) as source_url, marked primary; what others say about "
        "it is cited to them."),
    "shots": "",
    "gaps": SCOPES["explainer"]["gaps"],
    "outline": SCOPES["explainer"]["outline"] + (
        " The film is about the paper: place the problem it addresses and why "
        "it matters before the paper's mechanism, build on the paper's digest "
        "above, and keep what the paper itself shows apart from what others "
        "say it means."),
}


def claim_key(text: object) -> str:
    """A claim's text as two quotes of it are matched: case and whitespace
    folded, the first 120 characters. Nothing fuzzier: a quote that differs
    in its words is another claim."""
    return " ".join(str(text).lower().split())[:120]


_DOI, _ARXIV_ID, _HTTP = PAPER_DOI, PAPER_ARXIV, PAPER_HTTP


def _local_paper(ref: str) -> Path | None:
    """The local file a paper reference names, or None for a URL, a DOI or
    an arXiv id."""
    ref = str(ref or "").strip()
    return Path(ref).expanduser() if paper_is_local(ref) else None


def paper_to_keep(ref: object) -> str:
    """The reference as idea.json keeps it: as given, except a local file,
    kept by its absolute path so a later run from another folder still
    finds it. idea.json is local; the dossier names the file by its
    basename only (paper_label)."""
    ref = str(ref or "").strip()
    local = _local_paper(ref)
    return str(local.resolve()) if local is not None else ref


def paper_url(ref: str) -> str:
    """The URL a paper is cited by, everywhere it is cited: a DOI's
    https://doi.org/ address, an arXiv id's (or abs page's) PDF, any other
    http(s) URL as direct_file_url leaves it, and `file:<basename>` for a
    local file, whose path never enters the dossier."""
    ref = str(ref or "").strip()
    if ref.startswith("file:"):
        return ref
    if m := _DOI.match(ref):
        return f"https://doi.org/{m.group(1)}"
    if m := _ARXIV_ID.match(ref):
        return direct_file_url(f"https://arxiv.org/abs/{m.group(1)}")
    if _HTTP.match(ref):
        return direct_file_url(ref)
    return f"file:{Path(ref).name}"


def paper_label(ref: str) -> str:
    """The reference as the dossier and the prompts may name it: as given,
    except a local path, which is named by its basename."""
    return paper_url(ref) if _local_paper(ref) is not None else str(ref or "").strip()


def _paper_key(url: object) -> str:
    u = str(url or "").strip()
    m = re.match(r"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?/?$",
                 u, re.I)
    if m:
        return "arxiv:" + re.sub(r"v\d+$", "", m.group(1)).lower()
    m = re.match(r"^https?://(?:dx\.)?doi\.org/(.+?)/?$", u, re.I)
    if m:
        return "doi:" + urllib.parse.unquote(m.group(1)).lower()
    parsed = urlparse(u)
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return (parsed.netloc.lower() + parsed.path.rstrip("/")
                + (f"?{parsed.query}" if parsed.query else ""))
    return u


def same_paper(a: object, b: object) -> bool:
    """Whether two URLs name one paper: an arXiv abs page and its PDF, with
    or without a version suffix, or a DOI through either resolver; any other
    URL only as itself (scheme, host case and a trailing slash aside)."""
    key = _paper_key(a)
    return bool(key) and key == _paper_key(b)


#: Why a paper is refused under YT_ENGINE=codex: the read is evidenced by
#: the engine's own tool events, and codex reports neither of the two.
PAPER_REFUSAL = ("--paper needs the claude engine: codex reports no WebFetch/Read "
                 "events, so the paper read cannot be evidenced")


def paper_refusal() -> str:
    """PAPER_REFUSAL when the engine cannot evidence a paper read, else ""."""
    return PAPER_REFUSAL if harness.ENGINE_NAME == "codex" else ""


def scope_for(idea: Idea) -> dict:
    """Which research plan an idea gets, from its paper, genre and premise.

    An idea with a paper gets the paper scope, whatever its genre. A
    wordless piece -- cinematic, a music video, an ambient loop, "no
    narration" -- gets the lighter cinematic scope; everything else keeps the
    explainer scope that narrated videos have always had.
    """
    if str(getattr(idea, "paper", "") or "").strip():
        scope = dict(SCOPES["paper"])
        url = paper_url(idea.paper)
        scope["claims"] = scope["claims"].replace("{paper_url}", url)
        return scope
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

#: How a source presents a claim (AUTO-132), strongest first: a result it
#: reports as shown, a proposal or interpretation, a guess, and what it
#: names as a limitation, an open question, a preliminary result or future
#: work. It is not the source's reliability, which `confidence` and
#: `source_kind` record.
STANDINGS = ("established", "hypothesis", "speculation", "limitation")

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
                    # AUTO-132: optional, so a dossier written before it still
                    # reads; the screenplay then judges the claim from its
                    # confidence and caveat (script.ceiling).
                    "standing": {"type": "string", "enum": list(STANDINGS),
                                 "description": (
                                     "how the source itself presents the claim: a "
                                     "result it reports as shown, a proposal or "
                                     "interpretation, a guess, or a limitation, open "
                                     "question, preliminary result or future work it "
                                     "names. A value outside this list is asked again "
                                     "once, then read as no standing: the claim is "
                                     "judged from its confidence and caveat.")},
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
        # AUTO-90 (attention 7, 12): the belief the film breaks and a case it
        # can follow, each with the source that documents it, so the
        # screenplay's plan cites them instead of inventing them.
        "misconceptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "belief": {"type": "string"},
                    "source": {"type": "string",
                               "description": "where people are documented believing it"},
                },
                "required": ["belief", "source"],
            },
        },
        "identifiable_case": {
            "type": "object",
            "properties": {
                "who": {"type": "string",
                        "description": "a named person, place or event; empty for none"},
                "source": {"type": "string"},
            },
            "required": ["who", "source"],
        },
    },
    "required": ["working_title", "runtime", "sections", "misconceptions",
                 "identifiable_case"],
}


#: What the paper's digest (`06b-paper`, AUTO-131) holds about the paper,
#: in the order a film about it tells them.
PAPER_ITEMS = ("problem", "contribution", "significance", "implications",
               "consequences", "limitations")

_PAPER_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "one or two crew-facing sentences"},
        "claims": {"type": "array", "items": {"type": "string"}, "description": (
            "the claims it rests on, each quoted from the claims list above")},
    },
    "required": ["text", "claims"],
}

PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "citation": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "authors": {"type": "string"},
                           "year": {"type": "string"}, "url": {"type": "string"}},
            "required": ["title", "authors", "year", "url"],
        },
        **{key: _PAPER_ITEM for key in PAPER_ITEMS},
    },
    "required": ["citation", *PAPER_ITEMS],
}


def paper_problems(digest: dict, claims: list[dict], paper: str) -> list[str]:
    """What is wrong with a paper digest's form, one line each: an item with
    no text, an item that cites no claim, a quote that is none of the
    claims (case and whitespace aside), and a contribution that cites no
    claim sourced to the paper itself (`paper`, its URL). Whether the
    significance follows from the claims is the plan reviewer's to judge,
    not this check's."""
    known = {claim_key(c.get("claim", "")): c for c in claims or [] if isinstance(c, dict)}
    digest = digest if isinstance(digest, dict) else {}
    out = []
    for key in PAPER_ITEMS:
        entry = digest.get(key) if isinstance(digest.get(key), dict) else {}
        if not " ".join(str(entry.get("text") or "").split()):
            out.append(f"{key}: the text is empty")
        quotes = [str(q) for q in entry.get("claims") or [] if str(q).strip()]
        if not quotes:
            out.append(f"{key}: cites no claim")
            continue
        for quote in quotes:
            if claim_key(quote) not in known:
                out.append(f"{key}: cites \"{' '.join(quote.split())}\", which is not one "
                           "of the claims")
        if key == "contribution" and not any(
                same_paper(known[claim_key(q)].get("source_url"), paper)
                for q in quotes if claim_key(q) in known):
            out.append(f"contribution: cites no claim whose source is the paper itself "
                       f"({paper})")
    return out


def sourced_misconceptions(outline: dict, gaps: dict) -> list[dict]:
    """The beliefs the film breaks, as `{belief, source}`: the outline's, which
    carry the source that documents them, else the gap audit's, which name
    no source and say so with an empty one."""
    rows = [{"belief": str(m.get("belief") or "").strip(),
             "source": str(m.get("source") or "").strip()}
            for m in (outline or {}).get("misconceptions") or [] if isinstance(m, dict)]
    rows = [r for r in rows if r["belief"]]
    if rows:
        return rows
    return [{"belief": str(m).strip(), "source": ""}
            for m in (gaps or {}).get("misconceptions") or [] if str(m).strip()]


def case_of(outline: dict) -> dict:
    """The outline's identifiable case as `{who, source}`; an empty `who`
    when the research found none."""
    case = (outline or {}).get("identifiable_case")
    case = case if isinstance(case, dict) else {}
    return {"who": str(case.get("who") or "").strip(),
            "source": str(case.get("source") or "").strip()}


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
        # Written whole beside the target, then moved over it: the levels
        # save at the same time, and a reader never meets a half-written
        # stage.
        target = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.raw, prefix=f".{name}.", suffix=".tmp")
        os.close(fd)
        try:
            write_json(tmp, data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def drop(self, name: str) -> None:
        """Forget a cached stage, so the next run computes it again."""
        self._path(name).unlink(missing_ok=True)

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
        #: The structured judgements, one named decision each, kept under the
        #: project; the two web-agent passes are not decisions and book here.
        self.harness = harness.Harness(
            dossier.dir, "research", model=model, role=RESEARCHER_SYSTEM,
            fresh=fresh, verbose=verbose, budget_usd=budget)
        self._agent_usage = Usage()
        self.visited: list[str] = []
        #: The paper the film explains (AUTO-131), as given and as cited;
        #: both empty for a film that is not about one paper.
        self.paper_ref = str(getattr(idea, "paper", "") or "").strip()
        self.paper_url = paper_url(self.paper_ref) if self.paper_ref else ""
        #: Set once this run searched the catalogues again, so the outline
        #: that quotes the old assets is written again too.
        self.assets_searched = False
        #: Set when the cached research was made for another paper, or for
        #: none (read_paper): every stage the read feeds is made again.
        self.remade = False

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
        # A local paper is named by its basename (Idea.block): no prompt
        # that can echo it into a claim or the notes is told where it lives.
        return self.idea.block()

    def _stage(self, name: str, work, *, redo: bool = False) -> object:
        """Run `work()` unless this stage is already on disk (or `redo`, or
        it was fed by a paper read again: `remade`)."""
        redo = redo or (self.remade and name in self.PAPER_FED)
        if not (self.fresh or redo):
            cached = self.dossier.load(name)
            if cached is not None:
                self._say(f"[{name}] cached")
                return cached
        self._say(f"[{name}] running")
        data = work()
        self.dossier.save(name, data)
        return data

    @property
    def usage(self) -> Usage:
        """What the decisions and the web agents cost together."""
        total = Usage()
        _merge_usage(total, self.harness.usage)
        _merge_usage(total, self._agent_usage)
        return total

    def _decide(self, name: str, prompt: str, schema: dict) -> object:
        return self.harness.decide(name, prompt, schema)

    def _research_one(self, question: dict) -> dict:
        """One agentic web-research pass; records every URL actually fetched."""
        engine = harness.build_engine(self._config(web=True), ClaudeEngine)
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
            _merge_usage(self._agent_usage, engine.usage)
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

    #: The stages the paper's read feeds, every one after `00-paper`: cached
    #: for another paper, or for none, they would replay research about
    #: something else under this paper's name.
    PAPER_FED = ("01-questions", "02-research", "03-claims", "04-sources", "05-shots",
                 "05-assets", "06-gaps", "06b-paper", "07-outline")

    def read_paper(self) -> dict:
        """The paper itself, read in one agentic pass (`00-paper`) before any
        question is planned, outside every pool so a failure stops the run.
        A notes entry flagged `paper`. A cached read of another reference is
        read again, and so is everything it fed (`remade`); a dossier
        researched without a paper is researched again with it, since its
        questions were planned without the paper's (reception, limits)."""
        if why := paper_refusal():
            raise ClaudeError(why)
        asked = f"The paper itself: {paper_label(self.paper_ref)}"
        cached = None if self.fresh else self.dossier.load("00-paper")
        redo = isinstance(cached, dict) and cached.get("question") != asked
        before = [n for n in self.PAPER_FED if self.dossier.load(n) is not None] \
            if cached is None and not self.fresh else []
        if redo or before:
            was = (str(cached.get("question", "")).removeprefix("The paper itself: ")
                   if redo else "no paper")
            self._say(f"[00-paper] the cached research was made for {was}; researching "
                      f"again for {paper_label(self.paper_ref)}")
            self.remade = True
        return self._stage("00-paper", lambda: self._read_paper(asked), redo=redo)

    def _read_paper(self, asked: str) -> dict:
        """Validate-fail, never silent: the read stands only when the
        engine's own tool events show the paper opened -- a WebFetch whose
        URL is the paper's (same_paper; a DOI's doi.org address counts, the
        redirect never reaching tool_input), or a Read of the very file.
        A denied tool, or no such event, raises naming the reference."""
        ref, url = self.paper_ref, self.paper_url
        local = _local_paper(ref)
        target = local.resolve() if local is not None else None
        if target is not None and not target.is_file():
            raise ClaudeError(f"the paper {ref} is not a file here: --paper takes an "
                              "http(s) URL, a DOI, an arXiv id or a local PDF")
        config = EngineConfig(
            tools=("Read",) if target else ("WebFetch",), model=self.model,
            permission_mode="dontAsk", max_turns_budget_usd=self.budget,
            timeout=1800.0, cwd=str(target.parent) if target else None)
        engine = harness.build_engine(config, ClaudeEngine)
        ask = (f"Read the file `{target.name}` in the current directory with the Read "
               "tool: it is the paper. Read every page." if target else
               f"Read this paper for a YouTube video: {url}\n\nOpen it with WebFetch "
               "at exactly that URL. Do not search for it and do not answer from "
               "memory.")
        prompt = (
            f"{ask}\n\n{self._idea_block()}\n\n"
            "Write dense research notes on what the paper itself reports: the "
            "problem it addresses, the work before it that it names, its method, "
            "its findings with exact numbers, units and conditions, the "
            "limitations the authors state, and the full citation (title, "
            f"authors, year). Cite every fact to {url}. If the paper cannot be "
            "opened, or only an abstract is readable, say so plainly instead of "
            "filling in. No preamble, no summary of your process.")
        visited: list[str] = []
        notes: list[str] = []
        denied: list[dict] = []
        opened = False
        try:
            for event in engine.stream(prompt):
                if event.subtype == "tool_use" and event.tool_input:
                    if target is not None:
                        path = event.tool_input.get("file_path")
                        try:
                            hit = bool(path) and (target.parent / str(path)).resolve() == target
                        except (OSError, ValueError):
                            hit = False
                        if hit:
                            opened = True
                            if url not in visited:
                                visited.append(url)
                    elif event.tool_input.get("url"):
                        fetched = str(event.tool_input["url"])
                        visited.append(fetched)
                        opened = opened or same_paper(fetched, url)
                        self._say(f"    fetch {fetched[:90]}")
                if event.type == "assistant" and event.text:
                    notes.append(event.text)
                if event.type == "result":
                    denied = _denials(event)
        finally:
            _merge_usage(self._agent_usage, engine.usage)
        if denied:
            tools = ", ".join(sorted({d["tool"] for d in denied}))
            raise ClaudeError(f"the paper {ref} could not be read: {tools} denied, so its "
                              "notes would be written from memory")
        if not opened:
            how = (f"no Read of {target.name}" if target else f"no WebFetch of {url}")
            raise ClaudeError(f"the paper {ref} was never opened ({how} among the engine's "
                              "tool events), so its notes would be written from memory")
        text = "\n".join(notes).strip()
        if target is not None:
            # The agent may echo where the file lives; the dossier never says.
            given = Path(os.path.abspath(local))
            for where, said in ((str(target), url), (str(given), url), (str(local), url),
                                (str(target.parent), "."), (str(given.parent), "."),
                                (str(local.parent), ".")):
                if len(where) > 1:
                    text = text.replace(where, said)
        return {"question": asked, "notes": text, "visited": visited, "searches": 0,
                "denied": denied, "paper": True}

    # -- stages ------------------------------------------------------------

    def questions(self, paper: dict | None = None) -> dict:
        read = (f"What the paper says, as read:\n\n{self._notes_block([paper])}\n\n"
                if paper is not None and self.scope["name"] == "paper" else "")
        return self._stage("01-questions", lambda: self._decide(
            "01-questions",
            f"{self._idea_block()}\n\n{read}"
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
        return self._stage("03-claims", lambda: self._decide(
            "03-claims",
            f"{self._idea_block()}\n\nResearch notes:\n\n{self._notes_block(notes)}\n\n"
            + self.scope["claims"]
            + (f" The film is short: keep about {self.claim_ceiling} claims "
               "at most, the strongest ones." if self.claim_ceiling else ""),
            CLAIMS_SCHEMA))

    def sources(self, notes: list[dict]) -> dict:
        return self._stage("04-sources", lambda: self._decide(
            "04-sources",
            f"{self._idea_block()}\n\nResearch notes:\n\n{self._notes_block(notes)}\n\n"
            "Build the source list for this video's description and for "
            "fact-checking. One entry per distinct source actually present in "
            "the notes, deduplicated, best first. Include the publisher and "
            "year where the notes give them, and say in one clause what the "
            "video takes from each. Do not invent URLs."
            + (f" The paper itself is the first source, at {self.paper_url}."
               if self.scope["name"] == "paper" else ""),
            SOURCES_SCHEMA))

    def shots(self, notes: list[dict]) -> dict:
        """What the video must show. The model plans; it never supplies URLs."""
        return self._stage("05-shots", lambda: self._decide(
            "05-shots",
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

    def assets(self, notes: list[dict], sources: dict | None = None, *,
               plan: dict | None = None) -> dict:
        """Fill the shot list from the catalogues, verified live, and add the
        research's own open-access citations.

        `plan` is the shot list already decided; without one it is decided
        (or served) here. The cached search is replayed only when it was
        made the same way: one made with other catalogues, another licence
        policy or an older searcher never looked for what this one would
        find.
        """
        if plan is None:
            plan = self.shots(notes)
        key = self._asset_search_key()
        cached = None if self.fresh or self.remade else self.dossier.load("05-assets")
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
        verdict = self._decide(
            "assets-vet",
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
        engine = harness.build_engine(self._config(web=True), ClaudeEngine)
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
            _merge_usage(self._agent_usage, engine.usage)
        extra = self._decide(
            "assets-extract",
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
        return self._stage("06-gaps", lambda: self._decide(
            "06-gaps",
            f"{self._idea_block()}\n\n"
            f"Claims:\n{json.dumps(claims.get('claims', []), indent=2)}\n\n"
            f"Sources:\n{json.dumps(sources.get('sources', []), indent=2)}\n\n"
            + self.scope["gaps"],
            GAPS_SCHEMA))

    def paper_digest(self, claims: dict, sources: dict) -> dict:
        """What the paper means, as `06b-paper` decides it: its citation and
        PAPER_ITEMS, each citing claims quoted from `03-claims`. The digest
        is the author's extraction, checked here for form (paper_problems);
        a faulty one is asked once more (`06b-paper-again`) with the faults,
        and faulty twice, neither answer is kept and the run fails. Whether
        it follows from the claims is the plan reviewer's to judge."""
        rows = claims.get("claims", [])

        def work() -> dict:
            prompt = (
                f"{self._idea_block()}\n\n"
                f"Claims:\n{json.dumps(rows, indent=2)}\n\n"
                f"Sources:\n{json.dumps(sources.get('sources', []), indent=2)}\n\n"
                "Digest the paper this film explains. Give its citation (title, "
                f"authors, year, and {self.paper_url} as url), then, each in one or "
                "two crew-facing sentences resting on the claims above: the problem "
                "it addresses and what that costs in the world; its contribution, "
                "what it adds to what came before; its significance; its "
                "implications in the world; its broader consequences, what followed "
                "from it; and its limitations, open questions and what would refute "
                "it. Under each, quote the claims it rests on exactly as the claims "
                "list writes them. The contribution cites at least one claim the "
                f"paper itself reports (source_url {self.paper_url}). Keep what the "
                "paper itself shows apart from what others say it means.")
            digest = self._decide("06b-paper", prompt, PAPER_SCHEMA)
            problems = paper_problems(digest, rows, self.paper_url)
            if not problems:
                return digest
            self._say(f"[06b-paper] asking again ({'; '.join(problems)})")
            digest = self._decide(
                "06b-paper-again", prompt + "\n\nYour previous answer was rejected:\n"
                + "\n".join(f"- {line}" for line in problems)
                + "\nAnswer again, quoting only claims from the list above.", PAPER_SCHEMA)
            problems = paper_problems(digest, rows, self.paper_url)
            if problems:
                # Neither answer is usable; kept, they would fail every rerun.
                for name in ("06b-paper", "06b-paper-again"):
                    self.harness.forget(name)
                raise ClaudeError("the paper's digest is still faulty after asking "
                                  f"again: {'; '.join(problems)}")
            return digest
        return self._stage("06b-paper", work)

    def outline(self, claims: dict, assets: dict, gaps: dict,
                paper: dict | None = None) -> dict:
        # The outline quotes the assets each section shows; once the
        # catalogues were searched again, the cached one names the old set.
        used = sorted(a.get("url", "") for a in assets.get("assets", []))
        redo = self.assets_searched or \
            self.dossier.load("07-outline-assets") not in (None, used)
        digest = (f"The paper's digest:\n{json.dumps(paper, indent=2)}\n\n"
                  if paper is not None else "")
        outline = self._stage("07-outline", lambda: self._decide(
            "07-outline",
            f"{self._idea_block()}\n\n"
            f"Claims:\n{json.dumps(claims.get('claims', []), indent=2)}\n\n"
            f"Assets:\n{json.dumps(assets.get('assets', []), indent=2)}\n\n"
            f"Audit:\n{json.dumps(gaps, indent=2)}\n\n{digest}"
            + self.scope["outline"],
            OUTLINE_SCHEMA), redo=redo)
        self.dossier.save("07-outline-assets", used)
        return outline

    # -- run ---------------------------------------------------------------

    @staticmethod
    def _level(pool: ThreadPoolExecutor, works) -> list:
        """Run one dependency level at once. Every stage in it finishes (or
        fails) before the first failure is raised, so the ones that finished
        are on disk for the next run."""
        futures = [pool.submit(work) for work in works]
        wait(futures)
        return [future.result() for future in futures]

    def run(self) -> dict:
        # A paper is read before anything is planned, and its failure stops
        # the run (AUTO-131). Its entry leads the notes outside the cached
        # 02-research, so research cached before --paper still gets it.
        paper = self.read_paper() if self.paper_ref else None
        plan = self.questions(paper)
        notes = ([paper] if paper else []) + self.research(plan.get("questions", []))
        if paper:
            self.visited += paper.get("visited", [])
        # The decisions run in dependency levels on one pool, --jobs wide:
        # claims, sources and the shot list need only the notes; the asset
        # search needs the shots and the sources, the gap audit (and a
        # paper's digest) the claims and the sources; the outline needs all
        # of them. The stage names are unchanged, so a dossier researched in
        # series replays.
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            claims, sources, shots = self._level(pool, (
                lambda: self.claims(notes),
                lambda: self.sources(notes),
                lambda: self.shots(notes)))
            assets, gaps, *digest = self._level(pool, (
                lambda: self.assets(notes, sources, plan=shots),
                lambda: self.gaps(claims, sources),
                *([lambda: self.paper_digest(claims, sources)] if paper else [])))
        digest = digest[0] if digest else None
        outline = self.outline(claims, assets, gaps, digest)
        idea = asdict(self.idea)
        if paper:
            # A local paper's path stays in idea.json, never in the dossier.
            idea["paper"] = paper_label(self.paper_ref)
        else:
            idea.pop("paper", None)     # a film without one records the idea as before
        return {
            "idea": idea,
            "scope": self.scope["name"],
            "premise": plan.get("premise", ""),
            "questions": plan.get("questions", []),
            "shots": (self.dossier.load("05-shots") or {}).get("shots", []),
            "notes": notes,
            "claims": claims.get("claims", []),
            "sources": sources.get("sources", []),
            "assets": assets.get("assets", []),
            "gaps": gaps.get("gaps", []),
            "misconceptions": sourced_misconceptions(outline, gaps),
            "identifiable_case": case_of(outline),
            "verdict": gaps.get("verdict", ""),
            "outline": outline,
            # What the paper means, each item citing its claims (AUTO-131).
            **({"paper": digest} if digest is not None else {}),
            "urls_opened": sorted(set(self.visited)),
            "warnings": provenance_warnings(notes),
            "allow_sa": self.allow_sa,
            "usage": spend.from_usage(self.usage, self.model),
            "engine": self.harness.report(),
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


def _paper_provenance(entry: dict) -> list[str]:
    """What the paper's notes entry says about where its facts came from. A
    denied tool cannot normally reach here (read_paper raises on it)."""
    if entry.get("denied"):
        tools = sorted({str(d.get("tool", d)) if isinstance(d, dict) else str(d)
                        for d in entry["denied"]})
        return [f"the paper: {', '.join(tools)} denied, so it could not be read; its "
                "notes may be written from memory"]
    visited = [str(v) for v in entry.get("visited") or []]
    if not visited:
        return ["the paper: nothing was opened; its notes may be written from memory"]
    if all(v.startswith("file:") for v in visited):
        return [f"the paper: no public URL; the primary source is {visited[0]}"]
    return []


def provenance_warnings(notes: list[dict]) -> list[str]:
    """What, if anything, says these notes did not come from the web.

    Written into the dossier rather than only printed, so a later stage -- or
    `yt doctor` -- can see that a question was answered blind.
    """
    out = []
    n = 0
    for entry in notes:
        if entry.get("paper"):
            # The paper's own read (AUTO-131) is not a question: it shifts no
            # number, and says where its primary source is when it has no URL.
            out += _paper_provenance(entry)
            continue
        n += 1
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

    paper = data.get("paper")
    if isinstance(paper, dict) and paper:
        # What the paper means, each item with the claims it rests on (AUTO-131).
        cite = paper.get("citation") if isinstance(paper.get("citation"), dict) else {}
        md += ["## The paper", "",
               f"**{cite.get('title', '')}** — {cite.get('authors', '')}"
               + (f" ({cite['year']})" if cite.get("year") else "")
               + (f" · {cite['url']}" if cite.get("url") else ""), ""]
        for key in PAPER_ITEMS:
            entry = paper.get(key) if isinstance(paper.get(key), dict) else {}
            md.append(f"- **{key.capitalize()}.** {entry.get('text', '')}")
            md += [f"  - rests on: {c}" for c in entry.get("claims") or []]
        md.append("")

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
        for m in data["misconceptions"]:
            # {belief, source} since AUTO-90; a bare string in older dossiers
            belief, source = ((m.get("belief", ""), m.get("source", ""))
                              if isinstance(m, dict) else (m, ""))
            md.append(f"- {belief}" + (f" — {source}" if source else ""))
    case = data.get("identifiable_case") or {}
    if isinstance(case, dict) and case.get("who"):
        md += ["", "## A case to follow", "",
               f"- {case['who']}" + (f" — {case['source']}" if case.get("source") else "")]

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
           f"- cost: {spend.to_usage(data['usage'])}", ""]

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
                   help="questions researched, then decisions made, in "
                        "parallel (default 3)")
    flags.budget(p, default=None, dest="budget", metavar="USD", old=("--budget",),
                 help="hard spend cap in dollars per engine call")
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
    p.add_argument("--paper", metavar="REF",
                   help="the paper the film explains: an http(s) URL, a DOI, an arXiv "
                        "id or a local PDF, read first by the claude engine (WebFetch, "
                        "or Read for a file; refused under YT_ENGINE=codex) and kept "
                        "in idea.json")
    flags.fresh(p, help="redo every stage instead of reusing cached ones")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args)
    asked = str(getattr(args, "paper", None) or "").strip()
    if (asked or idea.paper) and (why := paper_refusal()):
        print(f"error: {why}", file=sys.stderr)
        return 2
    if asked:
        # Kept on the idea, so a later stage, or `yt run`, finds it there.
        idea.paper = paper_to_keep(asked)
        project.save_idea(idea)
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
