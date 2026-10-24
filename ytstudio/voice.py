"""Record the narration, measure it, and caption it.

The screenplay says what is spoken; this stage turns it into audio, finds out
how long it really takes, and writes the captions from the timings the engine
itself reports. Nothing downstream can be trusted until this runs: a shot's
runtime in the script is an estimate from a words-per-minute budget, and the
voice is the only thing that knows the truth.

    yt voice --title monty --engine espeak          # scratch take, no install
    yt voice --title monty --engine kokoro --lang p
    yt voice --title monty --engine chatterbox --reference my-voice.wav
    yt voice --title monty --engine elevenlabs --voice VOICE_ID --lang pt
    yt voice --title monty --dry-run                # characters, cost, no audio
    yt voice --title monty --draft                  # free read-aloud (rule 29)

Four engines, in the order `--engine auto` prefers them:

    elevenlabs  the hosted API, through super-tools' elevenlabs kit: exact
                word timings, prosody carried across cuts, paid per character
    kokoro      local open weights, fast, free, no cloning
    chatterbox  local, clones a voice from a short reference recording
    espeak      a formant synthesiser: robotic, instant, free. Use it to prove
                the timeline and the captions, never to publish.

Takes are cached by the hash of their text, engine, voice and settings, so
editing one line of the script re-records one shot. --redo forces a take.

Writes into the project: voice/ (one WAV per shot), voice.json (the manifest,
with per-shot and per-section drift against the script), narration.vtt and
narration.srt, and with --stitch a single voice.wav on the chosen layout.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import design, house, spend, workspace
from .harness import Harness, HarnessError, brief
from .core import Idea, read_json, timecode, write_json
from .script import Section, has_marks, load_script, mark_at, mark_tokens, shot_marks
from .script import sentences as split_sentences

#: Caption rule the delivery gate enforces: 17 characters per second.
CPS_LIMIT = 17.0

#: Speech sits here; the master normalisation in the scene brings the
#: programme to -14 LUFS once music and effects are in. This is RMS, not
#: LUFS -- a real loudness measurement happens in the delivery gate.
TARGET_RMS_DBFS = -20.0

#: What the hosted API will accept. PCM comes back headerless and is wrapped
#: as WAV here; anything else is kept as the engine sent it, which means no
#: level matching and no stitching without ffmpeg. Note the plan gates: 192
#: kbit/s needs Creator or above, and 44.1 kHz PCM needs Pro.
FORMAT_PREFIXES = ("pcm_", "mp3_", "opus_", "ulaw_", "alaw_")

#: Silence between shots and between sections, in seconds.
GAP = 0.35
SECTION_GAP = 0.8

#: Engines disagree about language codes -- kokoro wants one letter, espeak a
#: locale, ElevenLabs an ISO code -- so what the operator types is translated
#: rather than passed through to fail.
LANG_CODES = {
    "kokoro": {"pt": "p", "pt-br": "p", "en": "a", "en-us": "a", "en-gb": "b",
               "es": "e", "fr": "f", "hi": "h", "it": "i", "ja": "j", "zh": "z"},
    "espeak": {"pt": "pt-br", "pt-br": "pt-br", "en": "en-us", "en-us": "en-us",
               "en-gb": "en-gb", "es": "es", "fr": "fr", "it": "it"},
}

#: Default voice per engine and that engine's own language code.
DEFAULT_VOICES = {
    "kokoro": {"p": "pf_dora", "a": "af_heart", "b": "bf_emma", "e": "ef_dora"},
    "espeak": {"pt-br": "pt-br", "en-us": "en-us", "es": "es"},
}

#: The voices each engine ships, by its language code: what the casting
#: decision chooses from. Facts about an engine, not a preference.
KOKORO_VOICES = {
    "a": ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam",
          "am_michael", "am_fenrir", "am_puck"],
    "b": ["bf_emma", "bf_isabella", "bm_george", "bm_lewis"],
    "p": ["pf_dora", "pm_alex", "pm_santa"],
    "e": ["ef_dora", "em_alex", "em_santa"],
}

#: Playback speed a listener accepts without noticing the machine.
SPEED_RANGE = (0.85, 1.15)

ROLE = (
    "You cast and direct the voice-over of this film. From the voices the "
    "engine offers you choose the one whose character fits the treatment and "
    "the audience, set the pace, and give one delivery note per section (tone, "
    "where to slow down, what to stress). After the read you review the "
    "measurements -- drift per section, reads too fast for captions, overlaps "
    "-- and name the takes that must be recorded again, with the reason. A "
    "take is re-recorded for the listener's sake, never to hit an estimate."
)

CAST_SCHEMA = {
    "type": "object",
    "properties": {
        "voice": {"type": "string",
                  "description": "one of the candidate ids; empty only for an engine "
                                 "with a default voice (never elevenlabs)"},
        "speed": {"type": "number", "description": "0.85 to 1.15"},
        "settings": {"type": "object",
                     "description": "ElevenLabs voice settings, when that engine",
                     "properties": {"stability": {"type": "number"},
                                    "similarity_boost": {"type": "number"},
                                    "style": {"type": "number"}}},
        "delivery": {"type": "array",
                     "items": {"type": "object",
                               "properties": {
                                   "section": {"type": "string",
                                               "description": "the section's heading"},
                                   "note": {"type": "string"},
                                   # AUTO-139: optional, for a section whose
                                   # tone needs a read of its own.
                                   "speed": {"type": "number",
                                             "description": "0.85 to 1.15, this section "
                                                            "alone; omit to keep the cast's"},
                                   "settings": {
                                       "type": "object",
                                       "description": "ElevenLabs voice settings, this "
                                                      "section alone",
                                       "properties": {"stability": {"type": "number"},
                                                      "similarity_boost": {"type": "number"},
                                                      "style": {"type": "number"}}}},
                               "required": ["section", "note"]}},
        "why": {"type": "string"},
    },
    "required": ["voice", "speed", "why"],
}

#: The casting question a screenplay with no delivery marks is asked: the
#: one asked before AUTO-139, delivery as {section, note}. Its digest is
#: today's, so a project voiced before the marks keeps its kept cast (the
#: voice, speed and settings its takes were billed under) and is not cast
#: again; a marked screenplay is a new script and gets CAST_SCHEMA.
CAST_SCHEMA_PRE_MARKS = {
    **CAST_SCHEMA,
    "properties": {
        **CAST_SCHEMA["properties"],
        "delivery": {"type": "array",
                     "items": {"type": "object",
                               "properties": {"section": {"type": "string"},
                                              "note": {"type": "string"}},
                               "required": ["section", "note"]}}},
}

RETAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "retakes": {"type": "array",
                    "items": {"type": "object",
                              "properties": {
                                  "label": {"type": "string"},
                                  "reason": {"type": "string"},
                                  "speed": {"type": "number",
                                            "description": "0.85 to 1.15, this take alone"},
                                  "settings": {
                                      "type": "object",
                                      "description": "ElevenLabs voice settings, this take alone",
                                      "properties": {"stability": {"type": "number"},
                                                     "similarity_boost": {"type": "number"},
                                                     "style": {"type": "number"}}}},
                              "required": ["label", "reason"]}},
        "verdict": {"type": "string", "enum": ["ok", "retake"]},
        "notes": {"type": "string"},
    },
    "required": ["retakes", "verdict", "notes"],
}

WORDLESS_ROLE = (
    "You write the descriptive captions of a film that has no narration, for "
    "deaf and hard-of-hearing viewers: sound descriptions in square brackets, "
    "each naming what is heard and, where it matters, its character. You work "
    "from the sound cue sheet and the shots with their timings. You describe "
    "only sound that the cue sheet or the shot supports and you never invent "
    "dialogue or speech. One short cue per audible event, in the film's "
    "language, ordered in time, none overlapping, each long enough to read."
)

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "cues": {"type": "array",
                 "items": {"type": "object",
                           "properties": {
                               "at": {"type": "number",
                                      "description": "seconds from the top of the film"},
                               "seconds": {"type": "number",
                                           "description": "how long the cue stays up"},
                               "text": {"type": "string",
                                        "description": "the bracketed sound description"}},
                           "required": ["at", "seconds", "text"]}},
    },
    "required": ["cues"],
}

#: The delivery gate's caption limits (scene-render production_gate.captions).
GATE_LINE_CHARS = design.CAPTION_LINE_CHARS
GATE_LINES = design.CAPTION_LINES
GATE_CPS = design.CAPTION_CPS


#: The delivery controls each engine takes (AUTO-139): the screenplay's
#: marks (pronounce, pause, emphasis) and the director's per-section speed
#: and settings. A control an engine is not listed for is kept on the take as
#: `recorded_only`, never silently dropped. ElevenLabs' break tag is not
#: listed until it is checked against the kit's model (TO VERIFY).
MARK_SUPPORT = {
    "espeak": frozenset({"pronounce", "pause", "emphasis", "speed"}),
    "elevenlabs": frozenset({"pronounce", "speed", "settings"}),
    "kokoro": frozenset({"pronounce", "speed"}),
    "chatterbox": frozenset({"pronounce"}),
    "stub": frozenset(),
}

#: The silence a pause mark puts before its word, per engine that takes
#: one: direction 27's 300 ms, as espeak-ng's SSML says it.
BREAKS = {"espeak": '<break time="300ms"/>'}

#: Engines read SSML: the line is escaped and wrapped in <speak>.
SSML_ENGINES = ("espeak",)

#: What is stripped from a token's ends to find the word a mark names.
_EDGE = ".,;:!?\"'()[]{}“”‘’…—–"


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def respell(say: str, engine: str, *, stress: bool = True) -> str:
    """The engine's form of a human pronouncer: lowercased with its hyphens
    kept, so "SHROH-ding-er" is never read as an initialism or shouted. For
    an SSML engine it is XML-escaped and, with `stress`, its stressed
    (capitalised) syllables are wrapped in <emphasis>; a word already
    emphasised as a whole is not stressed inside. Always one token."""
    parts = str(say or "").split("-")
    if engine in SSML_ENGINES:
        return "-".join(f"<emphasis>{_xml(p.lower())}</emphasis>"
                        if stress and p and p.upper() == p and p.lower() != p
                        else _xml(p.lower())
                        for p in parts)
    return "-".join(p.lower() for p in parts)


def _split_token(token: str) -> tuple[str, str, str, str]:
    """A token as (leading punctuation, word, possessive, trailing
    punctuation): "(Schrödinger's," is ("(", "Schrödinger", "'s", ",")."""
    word = token.strip(_EDGE)
    if not word:
        return token, "", "", ""
    lead = token[:token.index(word)]
    tail = token[len(lead) + len(word):]
    poss = ""
    for mark in ("'s", "’s"):
        if word.endswith(mark) and len(word) > len(mark):
            word, poss = word[:-len(mark)], mark
            break
    return lead, word, poss, tail


def render_marks(text: str, marks: dict, engine: str) -> tuple[str, list[dict], list[dict]]:
    """The line as `engine` is to say it, from the shot's marks: (spoken,
    applied, recorded_only). `spoken` is "" when it would equal `text`.

    A pronouncer replaces each occurrence of its one-word term with the
    respelling, so one source token is always one spoken token; a pause puts
    the engine's break before its word; emphasis wraps its word, each in the
    first place at or after the previous one's. For espeak the line is
    XML-escaped and wrapped in <speak>. A mark the engine cannot take, or one
    on a word the line does not say, comes back in `recorded_only` with why.
    """
    support = MARK_SUPPORT.get(engine, frozenset())
    tokens = str(text or "").split()
    keys = mark_tokens(text)
    applied: list[dict] = []
    recorded: list[dict] = []
    said: dict[int, str] = {}
    stressed: set[int] = set()
    before: set[int] = set()

    def record(row: dict, why: str) -> None:
        recorded.append(row | {"why": why})

    for row in (marks or {}).get("pronounce") or []:
        term, say = str(row.get("term") or ""), str(row.get("say") or "")
        entry = {"kind": "pronounce", "term": term, "say": say}
        where = [i for i in range(len(keys)) if mark_at(term, keys, i) == i]
        if "pronounce" not in support:
            record(entry, f"{engine} takes no pronouncer")
        elif not where:
            record(entry, "not a word of the line")
        elif not say.strip():
            record(entry, "no say")
        elif len(term.split()) != 1:
            record(entry, "a term of more than one word would change the word count")
        else:
            said.update({i: say for i in where})
            applied.append(entry)
    for key, kind, into in (("emphasis", "emphasis", stressed), ("pauses", "pause", before)):
        cursor = 0
        for word in (marks or {}).get(key) or []:
            entry = {"kind": kind, "word": str(word)}
            at = mark_at(word, keys, cursor)
            at = at if at >= 0 else mark_at(word, keys, 0)
            if kind not in support:
                record(entry, f"{engine} takes no {kind}")
            elif at < 0:
                record(entry, "not a word of the line")
            else:
                into.add(at)
                applied.append(entry)
                cursor = at + 1
    if not applied:
        return "", [], recorded

    ssml = engine in SSML_ENGINES
    esc = _xml if ssml else (lambda s: s)
    out = []
    for i, token in enumerate(tokens):
        lead, word, poss, tail = _split_token(token)
        if i in said:
            word = respell(said[i], engine, stress=i not in stressed)
        else:
            word = esc(word)
        word += esc(poss)
        if i in stressed and ssml:
            word = f"<emphasis>{word}</emphasis>"
        piece = esc(lead) + word + esc(tail)
        if i in before and engine in BREAKS:
            piece = BREAKS[engine] + piece
        out.append(piece)
    spoken = " ".join(out)
    if ssml:
        spoken = f"<speak>{spoken}</speak>"
    return ("" if spoken == text else spoken), applied, recorded


def directions_from(retakes) -> dict[str, dict]:
    """What the director asked of each retake, as the recorder can apply it:
    label -> {"speed": within SPEED_RANGE, "settings": the known keys}. Rows
    without a label, or without a usable speed or settings, are dropped."""
    out: dict[str, dict] = {}
    for row in retakes if isinstance(retakes, list) else []:
        if not isinstance(row, dict) or not str(row.get("label") or "").strip():
            continue
        direction = _clamped_direction(row)
        if direction:
            out[str(row["label"]).strip()] = direction
    return out


def _clamped_direction(row: dict) -> dict:
    """A director's row as the recorder can apply it: `speed` within
    SPEED_RANGE, `settings` the known ElevenLabs keys; either left out when
    it is not usable."""
    direction: dict = {}
    try:
        speed = float(row["speed"])
        direction["speed"] = round(min(SPEED_RANGE[1], max(SPEED_RANGE[0], speed)), 3)
    except (KeyError, TypeError, ValueError):
        pass
    settings = row.get("settings")
    if isinstance(settings, dict):
        kept = {k: float(v) for k, v in settings.items()
                if k in ("stability", "similarity_boost", "style")
                and isinstance(v, (int, float))}
        if kept:
            direction["settings"] = kept
    return direction


def _fold_heading(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def delivery_from(cast: dict, sections: list[Section]) -> tuple[dict[str, dict], list[dict]]:
    """The director's per-section delivery as the recorder applies it:
    heading -> {note, speed?, settings?}, and the rows that match no section.
    A row matches a heading after whitespace and case folding, else a number
    is read as the 1-based section index. An unmatched row applies to no
    take; voice.json lists it."""
    rows = (cast or {}).get("delivery")
    by_heading: dict[str, dict] = {}
    unmatched: list[dict] = []
    headings = {_fold_heading(s.heading): s.heading for s in sections}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        asked = str(row.get("section") or "").strip()
        heading = headings.get(_fold_heading(asked))
        if heading is None and asked.isdigit() and 1 <= int(asked) <= len(sections):
            heading = sections[int(asked) - 1].heading
        if heading is None:
            unmatched.append({"section": asked, "note": str(row.get("note") or "")})
            continue
        by_heading[heading] = {"note": str(row.get("note") or "").strip(),
                               **_clamped_direction(row)}
    return by_heading, unmatched


def notes_only(cast: dict) -> dict:
    """A cast whose delivery rows keep their section and note alone: what a
    pre-marks screenplay's cast is used for, since its question offered no
    per-section speed or settings (an answer that gives one anyway would
    move takes into new calls, and re-bill them)."""
    rows = (cast or {}).get("delivery")
    return dict(cast or {}, delivery=[
        {"section": row.get("section", ""), "note": row.get("note", "")}
        for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)])


def casting_delivery(cast: dict) -> list[dict]:
    """casting.delivery as voice.json keeps it: each row's section and note
    as the director wrote them, with its speed and settings clamped."""
    rows = (cast or {}).get("delivery")
    return [{"section": row.get("section", ""), "note": row.get("note", ""),
             **_clamped_direction(row)}
            for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]


def delivery_summary(takes, unmatched: list[dict]) -> dict:
    """voice.json's `delivery`: how many controls the engine applied and
    how many it could only record, by kind, and the unmatched rows."""
    by_kind: dict[str, dict] = {}
    totals = {"applied": 0, "recorded_only": 0}
    for take in takes:
        delivery = (take.delivery if hasattr(take, "delivery") else take.get("delivery")) or {}
        for where in ("applied", "recorded_only"):
            for row in delivery.get(where) or []:
                kind = by_kind.setdefault(row.get("kind", "?"),
                                          {"applied": 0, "recorded_only": 0})
                kind[where] += 1
                totals[where] += 1
    return {**totals, "by_kind": by_kind, "unmatched": list(unmatched or [])}


#: What the take review hears of a take: its words, the recording and how
#: long it runs, and who read it.
HEARD_FIELDS = ("label", "text", "file", "seconds", "engine", "voice")


def heard(takes) -> dict:
    """The mark of the takes a take review heard: a digest of each take's
    HEARD_FIELDS, in order, and the count. `yt qa` computes it again from
    voice.json and uses the review as listening evidence only when the two
    agree (AUTO-105), so a review of takes since re-recorded never stands in
    for the read the film carries."""
    rows = []
    for take in takes or []:
        raw = asdict(take) if hasattr(take, "__dataclass_fields__") else dict(take or {})
        row = {key: raw.get(key) for key in HEARD_FIELDS}
        try:
            row["seconds"] = round(float(row["seconds"] or 0), 3)
        except (TypeError, ValueError):
            pass
        rows.append(row)
    text = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "takes": len(rows)}


def reviewed(review: dict, takes) -> dict:
    """The director's take review as voice.json keeps it: the answer, with
    `heard`, the mark of the takes it was asked about."""
    return dict(review or {}, heard=heard(takes))


def candidates(engine: str, lang: str) -> list[dict]:
    """The voices this engine offers for this language, as the editor sees them."""
    if engine == "kokoro":
        return [{"id": v, "about": ("female" if v[1] == "f" else "male")
                 + f" · {v.split('_', 1)[1]}"} for v in KOKORO_VOICES.get(lang, [])]
    if engine == "elevenlabs":
        # The operator's id=name list when set; else the voices in the
        # account, asked through the kit's client. ElevenLabs has no
        # default voice, so an empty list here means nothing can be cast.
        raw = os.environ.get("ELEVENLABS_VOICES", "")
        out = []
        for item in raw.split(","):
            if "=" in item:
                vid, name = item.split("=", 1)
                out.append({"id": vid.strip(), "about": name.strip()})
            elif item.strip():
                out.append({"id": item.strip(), "about": ""})
        return out or account_voices()
    if engine == "espeak":
        return [{"id": v, "about": "espeak-ng"} for v in DEFAULT_VOICES["espeak"].values()
                if v == lang]
    return []


def account_voices() -> list[dict]:
    """The voices the ElevenLabs account holds, as candidates: id, and what
    the editor needs to choose (name, kind, the labels the account keeps).
    An account that cannot be asked offers nothing, and says so upstream
    when a voice is required."""
    try:
        kit = _kit()
        client = kit.Client(kit.api_key())
        listing = client.get_json("/v2/voices", {"page_size": 100}) or {}
    except Exception:  # no kit, no key, no network: the caller decides
        return []
    out = []
    for v in listing.get("voices") or []:
        vid = str(v.get("voice_id") or "").strip()
        if not vid:
            continue
        labels = v.get("labels") or {}
        about = [str(v.get("name") or ""), str(v.get("category") or "")]
        about += [str(labels[k]) for k in ("gender", "age", "accent", "language",
                                            "description", "use_case")
                  if labels.get(k)]
        out.append({"id": vid, "about": " · ".join(a for a in about if a)})
    return out


class CastingError(RuntimeError):
    """No voice can be recorded with: the engine has no default and none
    was cast or forced."""


#: Engines that record with a voice of their own when none is named.
ENGINES_WITH_DEFAULT = ("kokoro", "espeak", "chatterbox", "stub")


def require_voice(engine: str, voice: str, offered: list[dict]) -> None:
    """Stop before the first request when the engine needs an id it has not
    got. An empty id would be sent in the request path and refused with a
    404 after the casting was paid for."""
    if voice or engine in ENGINES_WITH_DEFAULT:
        return
    choices = ", ".join(v["id"] for v in offered[:8])
    raise CastingError(
        f"{engine} has no default voice and none was cast. Pass --voice ID, set "
        f"ELEVENLABS_VOICES=id=name,... or add voices to the account"
        + (f"; the candidates offered were: {choices}" if choices else
           "; no candidates could be offered (is ELEVENLABS_API_KEY set and the "
           "account reachable?)") + ".")


def cast_instruction(engine: str, lang: str, *, marked: bool = True) -> str:
    """The first paragraph of the casting brief: what `voice` may be. A
    screenplay with no delivery marks (`marked` false) is asked it as it
    was before them, word for word, so its kept cast still serves."""
    default = ("the engine has no default: `voice` must be one of the candidate ids"
               if engine not in ENGINES_WITH_DEFAULT else
               "empty keeps the engine's default")
    return (f"Cast the narrator for this film and set the read. Engine: {engine}; "
            f"language: {lang or 'unspecified'}. Choose `voice` from the "
            f"candidates ({default}), `speed` within {SPEED_RANGE}, and one "
            + ("delivery note per section, naming it by its heading; give a section "
               "its own `speed` (and, for elevenlabs, `settings`) only where its tone "
               "needs a different read from the cast's." if marked else
               "delivery note per section."))


def apply_cast(cast: dict, offered: list[dict], *, engine: str,
               forced: str) -> tuple[str, dict]:
    """What of the casting decision is used: the voice only if the engine
    offers it (and never over --voice), the speed within range, and the
    settings only for the engine that has them."""
    ids = {row["id"] for row in offered}
    voice = forced or (str(cast.get("voice") or "")
                       if str(cast.get("voice") or "") in ids else "")
    try:
        speed = float(cast.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    opts = {"speed": round(min(SPEED_RANGE[1], max(SPEED_RANGE[0], speed)), 3)}
    settings = cast.get("settings")
    if engine == "elevenlabs" and isinstance(settings, dict) and settings:
        opts["settings"] = {k: float(v) for k, v in settings.items()
                            if k in ("stability", "similarity_boost", "style")
                            and isinstance(v, (int, float))}
    return voice, opts


def engine_lang(engine: str, lang: str) -> str:
    """The language code this engine understands, from the one typed."""
    return LANG_CODES.get(engine, {}).get((lang or "").lower(), lang)


# --------------------------------------------------------------------------- #
# audio, with the standard library only
# --------------------------------------------------------------------------- #

def write_wav(path: Path, frames: bytes, rate: int, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(frames)


def read_wav(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as fh:
        if fh.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM is handled")
        return fh.readframes(fh.getnframes()), fh.getframerate(), fh.getnchannels()


def wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as fh:
            return fh.getnframes() / float(fh.getframerate() or 1)
    except (OSError, wave.Error):
        return 0.0


def _samples(frames: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(frames) // 2}h", frames[:len(frames) // 2 * 2]))


def _numpy():
    """numpy when it is there: a full narration is millions of samples."""
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def rms_dbfs(frames: bytes) -> float | None:
    """Loudness as plain RMS. Not LUFS, and not a substitute for the gate."""
    if not frames:
        return None
    np = _numpy()
    if np is not None:
        data = np.frombuffer(frames[:len(frames) // 2 * 2], dtype="<i2")
        mean = float(np.mean(data.astype("f8") ** 2)) if data.size else 0.0
    else:
        data = _samples(frames)
        mean = sum(s * s for s in data) / len(data) if data else 0.0
    if mean <= 0:
        return None
    return 20.0 * math.log10((mean ** 0.5) / 32768.0)


def apply_gain(frames: bytes, db: float) -> bytes:
    """Scale every sample. Time-invariant, so word timings stay valid."""
    factor = 10.0 ** (db / 20.0)
    np = _numpy()
    if np is not None:
        data = np.frombuffer(frames[:len(frames) // 2 * 2], dtype="<i2")
        scaled = np.clip(data.astype("f4") * factor, -32768, 32767)
        return scaled.astype("<i2").tobytes()
    out = bytearray(len(frames) // 2 * 2)
    for i, sample in enumerate(_samples(frames)):
        struct.pack_into("<h", out, i * 2,
                         int(max(-32768, min(32767, round(sample * factor)))))
    return bytes(out)


def trim_silence(frames: bytes, rate: int, *, floor: int = 350,
                 keep: float = 0.08) -> bytes:
    """Cut dead air from both ends, keeping a little breath."""
    data = _samples(frames)
    if not data:
        return frames
    first = next((i for i, s in enumerate(data) if abs(s) > floor), 0)
    last = len(data) - next((i for i, s in enumerate(reversed(data))
                             if abs(s) > floor), 0)
    pad = int(keep * rate)
    first, last = max(0, first - pad), min(len(data), last + pad)
    return frames[first * 2:last * 2]


def silence(seconds: float, rate: int) -> bytes:
    return b"\x00\x00" * max(0, int(seconds * rate))


# --------------------------------------------------------------------------- #
# words and timings
# --------------------------------------------------------------------------- #

@dataclass
class Word:
    text: str
    start: float
    end: float


def words_from_alignment(path: Path) -> list[Word]:
    """Character timings from ElevenLabs, folded into words."""
    return _fold(_read_alignment(path))


def _read_alignment(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fold(data: dict, *, strip_tags: bool = False) -> list[Word]:
    """An alignment's character timings folded into words. `strip_tags`
    drops every <...> tag's characters first, so a break or SSML tag in the
    text that was sent counts as no word."""
    block = (data or {}).get("alignment") or (data or {}).get("normalized_alignment") or {}
    chars = block.get("characters") or []
    starts = block.get("character_start_times_seconds") or []
    ends = block.get("character_end_times_seconds") or []
    if not (chars and len(chars) == len(starts) == len(ends)):
        return []
    rows = list(zip(chars, starts, ends))
    if strip_tags:
        kept, inside = [], False
        for row in rows:
            if row[0] == "<":
                inside = True
            if not inside:
                kept.append(row)
            if row[0] == ">":
                inside = False
        rows = kept
    out: list[Word] = []
    buf, first, last = "", None, None
    for ch, a, b in rows:
        if ch.isspace():
            if buf:
                out.append(Word(buf, first, last))
                buf, first, last = "", None, None
            continue
        buf += ch
        first = a if first is None else first
        last = b
    if buf:
        out.append(Word(buf, first, last))
    return out


def words_estimated(text: str, seconds: float) -> list[Word]:
    """Spread words over the take by their length. Good enough to cut to."""
    tokens = text.split()
    if not tokens or seconds <= 0:
        return []
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights)
    out, cursor = [], 0.0
    for token, weight in zip(tokens, weights):
        span = seconds * weight / total
        out.append(Word(token, round(cursor, 3), round(cursor + span, 3)))
        cursor += span
    return out


def caption_words(take: Take, aligned: dict) -> tuple[list[Word], str]:
    """The caption words of a take from the engine's alignment, and their
    timing. An unmarked take is folded as it always was. A take sent as
    `spoken` has its tags stripped from the alignment and its aligned words
    mapped by position onto the line as written, one spoken token per source
    token, each keeping its timing; when the counts still differ the words
    are estimated over the take from `text`, timing "estimated", and the
    take's delivery records `caption_mismatch`. A spoken token never reaches
    a caption."""
    if not take.spoken:
        words = _fold(aligned)
        return words, ("exact" if words else "none")
    folded = _fold(aligned, strip_tags=True)
    source = take.text.split()
    if folded and len(folded) == len(source):
        return [Word(src, w.start, w.end) for src, w in zip(source, folded)], "exact"
    take.delivery = dict(take.delivery or {},
                         caption_mismatch={"spoken": len(folded), "text": len(source)})
    seconds = take.seconds or (folded[-1].end if folded else 0.0)
    words = words_estimated(take.text, seconds)
    # Nothing aligned and no length to spread the line over: no words, as
    # an unmarked take with no alignment has, not words of no duration.
    return words, ("estimated" if words else "none")


# --------------------------------------------------------------------------- #
# the take
# --------------------------------------------------------------------------- #

@dataclass
class Take:
    n: int
    label: str
    section: str
    text: str
    planned_at: int = 0
    planned_seconds: int = 0
    file: str = ""
    seconds: float = 0.0
    at: float = 0.0            # where it lands on this stage's own timeline
    engine: str = ""
    voice: str = ""
    timing: str = "none"       # exact | aligned | estimated
    rms_dbfs: float | None = None
    chars: int = 0
    cached: bool = False
    words: list[Word] = field(default_factory=list)
    # What the engine was sent when the marks changed it ("" when it is
    # `text`), its length, and the delivery: the section's note, the shot's
    # performance note, the marks, what the engine `applied` and what it
    # could only record (`recorded_only`). Captions and cps stay on `text`.
    spoken: str = ""
    spoken_chars: int = 0
    delivery: dict = field(default_factory=dict)

    @property
    def cps(self) -> float:
        return len(self.text) / self.seconds if self.seconds else 0.0

    @property
    def drift(self) -> float:
        return self.seconds - self.planned_seconds


# --------------------------------------------------------------------------- #
# engines
# --------------------------------------------------------------------------- #

def _kit():
    """super-tools' ElevenLabs client, wherever this checkout keeps it."""
    tried = []
    env = os.environ.get("ELEVENLABS_KIT")
    here = Path(__file__).resolve().parent.parent
    for candidate in ([Path(env)] if env else []) + [
            here.parent / "super-tools/tools/elevenlabs/elevenlabs_kit.py",
            here / "tools/elevenlabs/elevenlabs_kit.py"]:
        tried.append(str(candidate))
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("elevenlabs_kit", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    try:
        import elevenlabs_kit  # noqa: F401
        return elevenlabs_kit
    except ImportError:
        raise RuntimeError("the elevenlabs kit was not found. Set ELEVENLABS_KIT "
                           "to elevenlabs_kit.py, or keep super-tools beside "
                           f"this checkout. Tried: {', '.join(tried)}")


@dataclass
class Engine:
    name: str
    describe: str
    local: bool = True
    clones: bool = False

    def available(self) -> tuple[bool, str]:
        if self.name == "espeak":
            return (bool(shutil.which("espeak-ng")), "needs espeak-ng on PATH")
        if self.name == "stub":
            return True, ""
        if self.name == "elevenlabs":
            try:
                kit = _kit()
                kit.api_key()
                return True, ""
            except Exception as exc:  # the kit raises its own error type
                return False, str(exc).split("\n")[0]
        found = importlib.util.find_spec(self.name) is not None
        return found, f"pip install {self.name}"


ENGINES = {
    "elevenlabs": Engine("elevenlabs", "hosted API; exact word timings, paid "
                         "per character, prosody carried across cuts",
                         local=False, clones=True),
    "kokoro": Engine("kokoro", "local open weights; fast and free, no cloning"),
    "chatterbox": Engine("chatterbox", "local; clones a voice from a short "
                         "reference recording", clones=True),
    "espeak": Engine("espeak", "formant synthesiser; robotic but instant -- "
                     "proves the timeline, never publishes"),
    "stub": Engine("stub", "silence of the estimated length; for exercising "
                   "the pipeline with no engine at all"),
}

#: What --engine auto walks, best first.
PREFERENCE = ("elevenlabs", "kokoro", "chatterbox", "espeak", "stub")


def pick_engine(wanted: str) -> str:
    if wanted != "auto":
        return wanted
    for name in PREFERENCE:
        if ENGINES[name].available()[0]:
            return name
    return "stub"


# -- the synthesisers ------------------------------------------------------- #

def _synth_espeak(text: str, out: Path, *, voice: str, rate: int,
                  opts: dict) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    # The text goes in a file: as an argument, a line starting with "-" is
    # read as an option and the take fails.
    script_file = out.with_suffix(".txt")
    script_file.write_text(text, encoding="utf-8")
    # -m reads SSML, which only a marked take is (AUTO-139): an unmarked
    # take's argv is as it was.
    cmd = ["espeak-ng", "-v", voice or "en-us", "-s", str(opts.get("wpm", 165)),
           *(["-m"] if text.startswith("<speak>") else []),
           "-w", str(out), "-f", str(script_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    script_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"espeak-ng failed: {proc.stderr.strip()[:200]}")
    return {"timing": "estimated"}


def _synth_stub(text: str, out: Path, *, voice: str, rate: int,
                opts: dict) -> dict:
    seconds = max(0.6, len(text) / float(opts.get("cps", 14.0)))
    write_wav(out, silence(seconds, rate), rate)
    return {"timing": "estimated"}


def _synth_kokoro(text: str, out: Path, *, voice: str, rate: int,
                  opts: dict) -> dict:
    from kokoro import KPipeline  # optional dependency

    pipeline = KPipeline(lang_code=opts.get("lang") or "a")
    chunks = []
    for item in pipeline(text, voice=voice or "af_heart",
                         speed=float(opts.get("speed", 1.0))):
        audio = item[2] if isinstance(item, tuple) else item.audio
        chunks.append(audio)
    if not chunks:
        raise RuntimeError("kokoro produced no audio")
    frames = b"".join(_pcm16(chunk) for chunk in chunks)
    write_wav(out, frames, 24000)
    return {"timing": "estimated"}


def _synth_chatterbox(text: str, out: Path, *, voice: str, rate: int,
                      opts: dict) -> dict:
    reference = opts.get("reference")
    lang = opts.get("lang_id")
    model = None
    if lang:
        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS as M
            model = M.from_pretrained(device=opts.get("device", "cpu"))
        except ImportError:
            model = None
    if model is None:
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_pretrained(device=opts.get("device", "cpu"))
    kwargs = {}
    if reference:
        kwargs["audio_prompt_path"] = reference
    if lang:
        kwargs["language_id"] = lang
    wav = model.generate(text, **kwargs)
    write_wav(out, _pcm16(wav), int(getattr(model, "sr", 24000)))
    return {"timing": "estimated"}


def _pcm16(audio) -> bytes:
    """A torch tensor or numpy array of floats in [-1, 1] as 16-bit frames."""
    data = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
    data = data.reshape(-1)
    import numpy as np

    clipped = np.clip(data, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


SYNTH = {"espeak": _synth_espeak, "stub": _synth_stub, "kokoro": _synth_kokoro,
         "chatterbox": _synth_chatterbox}


# --------------------------------------------------------------------------- #
# the stage
# --------------------------------------------------------------------------- #

class VoiceOver:
    """Records every narrated shot, measures it, and reports the drift."""

    def __init__(self, idea: Idea, sections: list[Section], out_dir: Path, *,
                 engine: str = "auto", voice: str = "", lang: str = "",
                 rate: int = 24000, opts: dict | None = None,
                 layout: str = "sequential", gap: float = GAP,
                 section_gap: float = SECTION_GAP, normalise: bool = True,
                 trim: bool = True, redo: set[str] | None = None,
                 directions: dict[str, dict] | None = None,
                 delivery: dict[str, dict] | None = None,
                 verbose: bool = True) -> None:
        self.idea = idea
        self.sections = sections
        self.dir = out_dir
        self.engine = pick_engine(engine)
        self.lang = lang
        self.engine_lang = engine_lang(self.engine, lang)
        self.voice = voice or DEFAULT_VOICES.get(self.engine, {}).get(
            self.engine_lang or "", "")
        self.rate = rate
        self.opts = dict(opts or {})
        self.layout = layout
        self.gap = gap
        self.section_gap = section_gap
        self.normalise = normalise
        self.trim = trim
        self.redo = redo or set()
        self.directions = dict(directions or {})
        #: The director's per-section delivery, by heading (`delivery_from`).
        self.delivery = dict(delivery or {})
        self.verbose = verbose
        self.cache = self.dir / ".cache"

    def _say(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    # -- planning ----------------------------------------------------------

    def takes(self) -> list[Take]:
        """Every shot that has words, in order."""
        out = []
        for section in self.sections:
            for shot in section.shots:
                text = " ".join(shot.narration.split())
                if not text:
                    continue
                marks = shot_marks(shot)
                spoken, applied, recorded = render_marks(text, marks, self.engine)
                directed = self.delivery.get(section.heading) or {}
                support = MARK_SUPPORT.get(self.engine, frozenset())
                for kind in ("speed", "settings"):
                    if kind in directed:
                        row = {"kind": kind, "value": directed[kind]}
                        if kind in support:
                            applied.append(row)
                        else:
                            recorded.append(row | {"why": f"{self.engine} takes no {kind}"})
                out.append(Take(n=shot.n, label=shot.label,
                                section=section.heading, text=text,
                                planned_at=shot.at,
                                planned_seconds=shot.seconds,
                                chars=len(text), spoken=spoken,
                                spoken_chars=len(spoken or text),
                                delivery={"section_note": directed.get("note", ""),
                                          "performance": shot.note or "", **marks,
                                          "applied": applied,
                                          "recorded_only": recorded}))
        return out

    def _named(self, take: Take) -> bool:
        """Is this take one --redo asked to record again?"""
        return take.label in self.redo or "all" in self.redo

    def _direction(self, take: Take) -> dict:
        """The director's speed/settings for a named retake; nothing otherwise."""
        return self.directions.get(take.label, {}) if self._named(take) else {}

    def _section(self, take: Take) -> dict:
        """The director's speed/settings for this take's section, if any,
        as far as the engine takes them (MARK_SUPPORT): one it cannot take
        is only recorded on the take, and never moves its cache key."""
        row = self.delivery.get(take.section) or {}
        support = MARK_SUPPORT.get(self.engine, frozenset())
        return {k: row[k] for k in ("speed", "settings") if k in row and k in support}

    def _opts_for(self, take: Take) -> dict:
        """The engine options for one take: the cast's, then its section's
        delivery, then a named retake's direction, which wins. A section or
        retake speed replaces the cast's speed, never multiplies it."""
        opts = dict(self.opts)
        set_speed = None
        for layer in (self._section(take), self._direction(take)):
            if "speed" in layer:
                opts["speed"] = set_speed = layer["speed"]
            if "settings" in layer:
                opts["settings"] = (opts.get("settings") or {}) | layer["settings"]
        if self.engine == "espeak" and set_speed is not None:
            # espeak-ng takes a rate, not a speed: it is scaled by the asked
            # speed over the cast's, and only when one is asked, so a film
            # with no section or retake speed sends today's -s.
            cast = float(self.opts.get("speed") or 1.0)
            opts["wpm"] = round(float(self.opts.get("wpm", 165)) * set_speed / cast)
        return opts

    def _fingerprint(self, take: Take) -> str:
        parts = [self.engine, self.voice, self.lang, str(self.rate),
                 json.dumps(self._opts_for(take), sort_keys=True, default=str),
                 # What the engine hears: an unmarked take keeps today's key,
                 # so its cached WAV still serves.
                 take.spoken or take.text]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    # -- recording ---------------------------------------------------------

    def _record_local(self, takes: list[Take]) -> None:
        synth = SYNTH[self.engine]
        for take in takes:
            target = self.dir / f"{take.n:03d}-{take.label.replace('.', '-')}.wav"
            cached = self.cache / f"{self._fingerprint(take)}.wav"
            if cached.exists() and not self._named(take):
                take.cached = True
            else:
                self._say(f"  {take.label} {len(take.text):>4} chars")
                synth(take.spoken or take.text, cached, voice=self.voice, rate=self.rate,
                      opts=self._opts_for(take) | {"lang": self.engine_lang})
            frames, rate, channels = read_wav(cached)
            if self.trim:
                frames = trim_silence(frames, rate)
            level = rms_dbfs(frames)
            if self.normalise and level is not None:
                frames = apply_gain(frames, TARGET_RMS_DBFS - level)
                level = rms_dbfs(frames)
            write_wav(target, frames, rate, channels)
            take.file = target.name
            take.seconds = round(len(frames) / 2 / channels / rate, 3)
            take.rms_dbfs = round(level, 1) if level is not None else None
            take.engine, take.voice = self.engine, self.voice
            take.timing = "estimated"
            take.words = words_estimated(take.text, take.seconds)

    def _retrying(self, call, *, attempts: int = 4, delay: float = 20.0):
        """Run `call`, waiting out a rate limit instead of losing the run.

        The kit asks for the subscription once per take, so a long script is a
        burst of requests and 429 is expected. Retrying is free: every take
        already written is served from the kit's cache and is not paid again.
        """
        for attempt in range(1, attempts + 1):
            try:
                return call()
            except Exception as exc:
                limited = "429" in str(exc) or "rate_limit" in str(exc).lower()
                if not limited or attempt == attempts:
                    raise
                self._say(f"  rate limited by the API; waiting {delay:.0f}s "
                          f"and resuming (attempt {attempt + 1} of {attempts}; "
                          "finished takes are cached, nothing is paid twice)")
                time.sleep(delay)
                delay *= 2

    def _record_elevenlabs(self, takes: list[Take]) -> None:
        kit = _kit()
        client = kit.Client(kit.api_key())
        # The engine is sent what the marks made of the line (AUTO-139); an
        # unmarked take sends its text, as before.
        scenes = [{"id": f"{t.n:03d}-{t.label.replace('.', '-')}",
                   "text": t.spoken or t.text} for t in takes]
        self.dir.mkdir(parents=True, exist_ok=True)
        fmt = self.opts.get("format") or f"pcm_{self.rate}"
        base = self.opts.get("settings") or None

        def send(group: list[int], *, force: bool, settings) -> None:
            if not group:
                return
            scenes_file = self.dir / "scenes.json"
            scenes_file.write_text(
                json.dumps([scenes[i] for i in group], ensure_ascii=False, indent=1),
                encoding="utf-8")
            # tts_scenes carries each shot's neighbours and request ids, so
            # the prosody runs across the cuts instead of resetting every shot.
            call = lambda: kit.tts_scenes(  # noqa: E731
                client, str(scenes_file), self.voice, str(self.dir),
                model=self.opts.get("model") or kit.DEFAULT_MODEL,
                fmt=fmt, language=self.lang or None, timestamps=True,
                settings=settings, max_chars=self.opts.get("max_chars"),
                force=force, log=self._say)
            self._retrying(call)

        # The kit takes one force flag per call, so the named takes are
        # recorded apart: the accepted ones are served from its cache (not
        # paid, not replaced) and a retake costs only itself.
        # The takes not named and the named ones without a direction are
        # grouped by the settings their section gives them (AUTO-139), one
        # call per distinct value in film order: with no section delivery
        # every take has the cast's settings and the calls are as before.
        # The cast's speed is never sent; a section's or a retake's is,
        # inside the settings of its takes alone.
        def effective(take: Take):
            section = self._section(take)
            if not section:
                return base
            settings = (base or {}) | section.get("settings", {})
            if "speed" in section:
                settings = settings | {"speed": section["speed"]}
            return settings or None

        for force, members in ((False, [i for i, t in enumerate(takes) if not self._named(t)]),
                               (True, [i for i, t in enumerate(takes)
                                       if self._named(t) and not self._direction(t)])):
            groups: dict[str, tuple] = {}
            for i in members:
                settings = effective(takes[i])
                key = json.dumps(settings, sort_keys=True)
                groups.setdefault(key, (settings, []))[1].append(i)
            for settings, group in groups.values():
                send(group, force=force, settings=settings)
        for i, take in enumerate(takes):
            direction = self._direction(take)
            if direction:
                settings = self._opts_for(take).get("settings") or {}
                speed = direction.get("speed", self._section(take).get("speed"))
                if speed is not None:
                    settings = settings | {"speed": speed}
                send([i], force=True, settings=settings or None)
        for take, scene in zip(takes, scenes):
            raw = self.dir / f"{scene['id']}.mp3"
            align = self.dir / f"{scene['id']}.alignment.json"
            take.engine, take.voice = self.engine, self.voice
            take.words = [] if take.spoken else words_from_alignment(align)
            take.timing = "exact" if take.words else "none"
            if fmt.startswith("pcm_") and raw.exists():
                # PCM comes back headerless, under the kit's own .mp3 name;
                # wrap it as WAV beside it. The original stays: the kit's cache
                # checks the file and its sha256, so deleting it would re-bill
                # every take on the next run.
                target = raw.with_suffix(".wav")
                frames = raw.read_bytes()
                level = rms_dbfs(frames)
                if self.normalise and level is not None:
                    # Gain only, never a trim: the alignment's timings are
                    # measured from the head of this audio.
                    frames = apply_gain(frames, TARGET_RMS_DBFS - level)
                    level = rms_dbfs(frames)
                write_wav(target, frames, int(fmt.split("_")[1]))
                take.file = target.name
                take.seconds = round(wav_seconds(target), 3)
                take.rms_dbfs = round(level, 1) if level is not None else None
            else:
                take.file = raw.name
                take.seconds = round(take.words[-1].end, 3) if take.words else 0.0
            if take.spoken:
                # The alignment is of what was sent: the captions take the
                # line's own words at its timings, or estimate them.
                aligned = _read_alignment(align)
                if not take.seconds:
                    folded = _fold(aligned, strip_tags=True)
                    take.seconds = round(folded[-1].end, 3) if folded else 0.0
                take.words, take.timing = caption_words(take, aligned)
                if not take.words:
                    take.timing = "none"

    def run(self) -> dict:
        takes = self.takes()
        if not takes:
            raise RuntimeError("no narration in the screenplay to record")
        self._say(f"[voice] {self.engine}"
                  + (f" · {self.voice}" if self.voice else "")
                  + f" · {len(takes)} takes · {sum(t.chars for t in takes)} chars"
                  + spoken_note(takes))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        if self.engine == "elevenlabs":
            self._record_elevenlabs(takes)
        else:
            self._record_local(takes)

        cursor, last_section = 0.0, None
        for take in takes:
            if self.layout == "planned":
                take.at = float(take.planned_at)
            else:
                if last_section is not None and take.section != last_section:
                    cursor += self.section_gap - self.gap
                take.at = round(cursor, 3)
                cursor += take.seconds + self.gap
            last_section = take.section

        return {"idea": asdict(self.idea), "engine": self.engine,
                "voice": self.voice, "lang": self.lang,
                "engine_lang": self.engine_lang, "layout": self.layout,
                "takes": [asdict(t) for t in takes],
                **summarise(takes, layout=self.layout)}


def spoken_note(takes: list[Take]) -> str:
    """" (N sent to the engine)" when the marks made what the engine is
    sent longer or shorter than the lines; "" otherwise, so an unmarked
    film prints as it did."""
    chars = sum(t.chars for t in takes)
    sent = sum(t.spoken_chars or t.chars for t in takes)
    return f" ({sent} sent to the engine)" if sent != chars else ""


def summarise(takes: list[Take], *, layout: str) -> dict:
    """Totals and drift, measured rather than asked for."""
    spoken = sum(t.seconds for t in takes)
    planned = sum(t.planned_seconds for t in takes)
    end = max((t.at + t.seconds for t in takes), default=0.0)
    sections: dict[str, dict] = {}
    for take in takes:
        row = sections.setdefault(take.section, {"planned": 0.0, "spoken": 0.0,
                                                 "takes": 0})
        row["planned"] += take.planned_seconds
        row["spoken"] += take.seconds
        row["takes"] += 1
    for row in sections.values():
        row["drift"] = round(row["spoken"] - row["planned"], 2)
        row["planned"], row["spoken"] = round(row["planned"], 2), round(row["spoken"], 2)
    hot = [{"label": t.label, "cps": round(t.cps, 1), "seconds": t.seconds,
            "text": t.text[:80]} for t in takes if t.cps > CPS_LIMIT]
    rows = sorted(takes, key=lambda t: t.at)
    overlaps = [{"label": a.label, "next": b.label,
                 "by": round(a.at + a.seconds - b.at, 2)}
                for a, b in zip(rows, rows[1:]) if a.at + a.seconds > b.at]
    return {
        "spoken_seconds": round(spoken, 2),
        "planned_seconds": planned,
        "drift_seconds": round(spoken - planned, 2),
        "voice_end": round(end, 3),
        "timing": sorted({t.timing for t in takes}),
        "by_section": sections,
        "too_fast_for_captions": hot,
        "overlaps": overlaps,
    }


# --------------------------------------------------------------------------- #
# captions and the stitched track
# --------------------------------------------------------------------------- #

def caption_lines(text: str, width: int = design.CAPTION_LINE_CHARS) -> list[str]:
    return design.wrap_lines(text, width)


def _stamp(seconds: float, *, comma: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_captions(path: Path, takes: list[Take], *, srt: bool = False,
                   film_end: float | None = None) -> tuple[int, int]:
    """The cues of `design.caption_cues`, on the voice's own timeline.

    Each take is split on its word timings into cues of at most two lines,
    and a cue is clamped so it never runs into the next one: overlapping cues
    are invalid in both formats, and the planned layout produces them whenever
    a take came out longer than the script budgeted. Returns how many were
    clamped and how many ended up under the 0.7 s the delivery gate wants.
    A cue that cannot be made legal is warned about, not hidden.
    """
    cues, clamped, illegal = design.caption_cues([asdict(t) for t in takes],
                                                 film_end=film_end)
    if illegal:
        print(f"warning: {illegal} caption(s) cannot be made legal (a single word "
              f"over {design.CAPTION_CPS:g} characters per second); delivery will "
              "flag them", file=sys.stderr)
    too_short = sum(1 for c in cues if c.end - c.start < design.CAPTION_MIN_SECONDS)
    out = [] if srt else ["WEBVTT", ""]
    for i, cue in enumerate(cues, 1):
        if srt:
            out.append(str(i))
        out.append(f"{_stamp(cue.start, comma=srt)} --> {_stamp(cue.end, comma=srt)}")
        out += caption_lines(cue.text)
        out.append("")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return clamped, too_short


def write_cues(path: Path, cues: list[dict], *, srt: bool = False) -> None:
    """Legal cues (see `legalise`) as VTT or SRT."""
    out = [] if srt else ["WEBVTT", ""]
    for i, cue in enumerate(cues, 1):
        if srt:
            out.append(str(i))
        out.append(f"{_stamp(cue['start'], comma=srt)} --> {_stamp(cue['end'], comma=srt)}")
        out += cue["lines"]
        out.append("")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def legalise(raw: list[dict], film_end: float) -> tuple[list[dict], list[str]]:
    """Make descriptive cues legal to the delivery gate, mechanically.

    Ordered and not overlapping, inside 0..film_end, at most two lines of at
    most 42 characters, at most 17 characters per second. A cue too fast is
    lengthened into the gap before the next one; one that cannot be made legal
    is dropped. The words are never changed. Returns the cues
    ({start, end, lines}) and one sentence for everything that was altered.
    """
    notes: list[str] = []
    end_ms = int(film_end * 1000)
    rows = []
    for item in raw:
        try:
            at, length = float(item.get("at")), float(item.get("seconds") or 0)
        except (TypeError, ValueError, AttributeError):
            notes.append(f"dropped a cue with no usable time: {str(item)[:60]}")
            continue
        text = " ".join(str(item.get("text", "")).split())
        if not text or not math.isfinite(at) or not math.isfinite(length):
            notes.append(f"dropped an empty or untimed cue at {at}")
            continue
        rows.append((at, length, text))
    rows.sort(key=lambda r: r[0])

    cues: list[dict] = []
    prev_end = 0
    for i, (at, length, text) in enumerate(rows):
        label = f"{text[:30]!r} at {at:g}s"
        lines = caption_lines(text, GATE_LINE_CHARS)
        if len(lines) > GATE_LINES or any(len(x) > GATE_LINE_CHARS for x in lines):
            notes.append(f"dropped {label}: more than {GATE_LINES} lines of "
                         f"{GATE_LINE_CHARS} characters")
            continue
        start = max(int(round(at * 1000)), 0, prev_end)
        asked_end = int(round((at + length) * 1000))
        if start >= end_ms:
            notes.append(f"dropped {label}: outside the film's {film_end:g}s")
            continue
        need = _need_ms(lines)
        want = max(asked_end - start, need, 700)
        nxt = next((int(round(r[0] * 1000)) for r in rows[i + 1:]
                    if int(round(r[0] * 1000)) > start), end_ms)
        end = min(start + want, nxt, end_ms)
        if end - start < need:
            moved = []
            # Borrow before dropping: first from the gap behind (the previous
            # cue's end comes back while it keeps its own minimum), then from
            # the cue ahead (its start goes later, keeping its minimum and order).
            floor = cues[-1]["start_ms"] + cues[-1]["min_ms"] if cues else 0
            back = min(max(start - floor, 0), need - (end - start))
            if back > 0:
                start -= back
                moved.append(f"started {back / 1000:.2f}s earlier")
            if end - start < need and i + 1 < len(rows):
                after = rows[i + 1]
                after_min = max(_need_ms(caption_lines(after[2], GATE_LINE_CHARS)), 700)
                after_cap = (int(round(rows[i + 2][0] * 1000))
                             if i + 2 < len(rows) else end_ms) - after_min
                pushed = min(max(start + need, end), max(after_cap, end), end_ms)
                if pushed > end:
                    moved.append(f"pushed the start of the cue after it "
                                 f"{(pushed - nxt) / 1000:.2f}s later")
                    if pushed > round(after[0] * 1000):
                        rows[i + 1] = (pushed / 1000,
                                       max(after[0] + after[1] - pushed / 1000, 0.0),
                                       after[2])
                    end = pushed
            if end - start < need:
                notes.append(f"dropped {label}: needs {need / 1000:.2f}s at "
                             f"{GATE_CPS:g} characters per second and only "
                             f"{(end - start) / 1000:.2f}s fits even after "
                             "borrowing from the cues around it")
                continue
            if cues and cues[-1]["end_ms"] > start:
                cues[-1]["end_ms"] = start
            notes.append(f"kept {label} by borrowing slack: " + "; ".join(moved))
            asked_end = end
        if end > asked_end:
            notes.append(f"lengthened {label} to {(end - start) / 1000:.2f}s "
                         "(reading speed)")
        elif end < asked_end:
            notes.append(f"shortened {label} to {(end - start) / 1000:.2f}s "
                         "(next cue or the end of the film)")
        cues.append({"start_ms": start, "end_ms": end, "lines": lines,
                     "min_ms": max(need, 700)})
        prev_end = end
    legal = [{"start": c["start_ms"] / 1000, "end": c["end_ms"] / 1000,
              "lines": c["lines"]} for c in cues]
    return legal, notes


def _need_ms(lines: list[str]) -> int:
    """Shortest legal on-screen time for these lines at the gate's reading speed."""
    return int(math.ceil(sum(len(x) for x in lines) / GATE_CPS * 1000)) + 2


def wordless_timeline(sections: list[Section]) -> tuple[list[dict], float, float]:
    """The shots as the pipeline places them, the film's end, and the last
    shot's start (the voice end of a film with no voice)."""
    from . import build
    placed = build.timeline(sections, None)
    shots = [{"label": p.label, "section": p.section, "at": p.at, "seconds": p.seconds}
             for p in placed]
    end = max((p.at + p.seconds for p in placed), default=0.0)
    return shots, round(end, 3), round(max((p.at for p in placed), default=0.0), 3)


def wordless(idea: Idea, sections: list[Section], director: Harness,
             sound: dict) -> tuple[dict, list[dict], list[str]]:
    """Descriptive captions and the manifest for a screenplay with no narration."""
    shots, film_end, last_start = wordless_timeline(sections)
    visuals = {sh.label: sh.visual[:200] for sec in sections for sh in sec.shots}
    for row in shots:
        row["visual"] = visuals.get(row["label"], "")
    cues = [{k: c.get(k) for k in ("kind", "name", "at", "seconds", "shot",
                                   "description", "purpose")}
            for c in (sound or {}).get("cues", []) if isinstance(c, dict)]
    answer = director.decide("describe", "\n\n".join([
        "This film has no narration. Write its descriptive captions: bracketed "
        "sound descriptions for deaf and hard-of-hearing viewers, one cue per "
        "audible event, in time order, none overlapping. Name what is heard "
        "and its character; never invent dialogue. Keep each cue under "
        f"{GATE_LINES * GATE_LINE_CHARS} characters, and give it enough seconds "
        f"to read at {GATE_CPS:g} characters per second. The film runs "
        f"{film_end:g} seconds.",
        brief("Film", {"title": idea.title, "premise": idea.description,
                       "genre": idea.genre}),
        brief("Sound cue sheet", cues),
        brief("Shots and their timings", shots)]), DESCRIBE_SCHEMA)
    asked = answer.get("cues", [])
    legal, notes = legalise(asked, film_end)
    data = {"idea": asdict(idea), "engine": "none", "voice": "", "lang": "",
            "layout": "none", "wordless": True, "takes": [],
            "voice_end": last_start, "voice_end_source": "last shot start",
            "film_end": film_end, "spoken_seconds": 0.0, "planned_seconds": 0,
            "drift_seconds": 0.0, "timing": [], "by_section": {},
            "too_fast_for_captions": [], "overlaps": [],
            "descriptive_captions": {"cues": len(legal), "asked": len(asked),
                                     "repairs": notes}}
    return data, legal, notes


def stitch(path: Path, takes: list[Take], source: Path) -> tuple[float, list[str]]:
    """One continuous track with the takes where the layout puts them.

    The rate comes from the takes themselves, not from what was asked for: an
    engine gives what it gives (espeak-ng writes 22050 Hz whatever you ask),
    and silently dropping the takes that disagree is how you get a 0-second
    track. Resampling is ffmpeg's job, so a take at another rate is skipped
    and named.
    """
    wavs = [(t, source / t.file) for t in takes
            if (source / t.file).exists() and (source / t.file).suffix == ".wav"]
    if not wavs:
        write_wav(path, b"", 24000)
        return 0.0, ["no WAV takes to stitch"]
    _, first = wavs[0]
    rate, channels = read_wav(first)[1:]
    frames, skipped = bytearray(), []
    for take, wav in wavs:
        data, take_rate, take_channels = read_wav(wav)
        if (take_rate, take_channels) != (rate, channels):
            skipped.append(f"{take.label} at {take_rate} Hz/{take_channels} ch")
            continue
        want = int(take.at * rate) * 2 * channels
        if len(frames) < want:
            frames += silence((want - len(frames)) / 2 / channels / rate, rate)
        frames += data
    write_wav(path, bytes(frames), rate, channels)
    return wav_seconds(path), skipped


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def print_voice(data: dict, takes: list[Take]) -> None:
    print(f"\n{data['idea']['title']} — NARRATION")
    print(f"{data['engine']}"
          + (f" · {data['voice']}" if data.get("voice") else "")
          + f" · {len(takes)} takes · timing: {', '.join(data['timing'])}"
          + (f" · {sum(t.chars for t in takes)} chars{spoken_note(takes)}"
             if spoken_note(takes) else ""))
    print(f"spoken {timecode(data['spoken_seconds'])} · "
          f"planned {timecode(data['planned_seconds'])} · "
          f"drift {data['drift_seconds']:+.1f}s · "
          f"voice ends {data['voice_end']:.1f}s")
    told = data.get("delivery") or {}
    if told.get("applied") or told.get("recorded_only") or told.get("unmatched"):
        print(f"delivery: {told.get('applied', 0)} applied · "
              f"{told.get('recorded_only', 0)} recorded only"
              + (f" · {len(told['unmatched'])} note(s) naming no section"
                 if told.get("unmatched") else ""))

    section = None
    for take in takes:
        if take.section != section:
            section = take.section
            row = data["by_section"][section]
            print(f"\n  {section}  [{row['spoken']:.1f}s spoken vs "
                  f"{row['planned']:.0f}s planned, {row['drift']:+.1f}s]")
        flag = "  !" if take.cps > CPS_LIMIT else ""
        print(f"    {take.label:<6} {timecode(int(take.at))}  "
              f"{take.seconds:>5.1f}s  {take.cps:>4.1f} cps{flag}  "
              + ("[cache] " if take.cached else "")
              + take.text[:46] + ("…" if len(take.text) > 46 else ""))

    if data["too_fast_for_captions"]:
        print(f"\n  over {CPS_LIMIT:.0f} characters per second "
              "(the delivery gate rejects these):")
        for row in data["too_fast_for_captions"]:
            print(f"    {row['label']}  {row['cps']} cps  {row['text']}")


# --------------------------------------------------------------------------- #
# the read-aloud draft (direction 29)
# --------------------------------------------------------------------------- #

#: Where `yt voice --draft` writes, under the project. No stage reads it as
#: the voice: it is the free read the paid one is gated on.
DRAFT_DIR, DRAFT_FILE = "voice-draft", "voice-draft.json"
#: The free readers, best first. A draft never pays and is never silence.
DRAFT_READERS = ("kokoro", "espeak")
#: Direction 29's number: a sentence that takes longer than this to read
#: aloud is longer than a listener holds. Practitioner, not calibrated.
DRAFT_SENTENCE_LIMIT = 10.0
_DRAFT_NUMERAL = re.compile(r"\d(?:[\d,.]*\d)?")


def script_sha(sections: list[Section]) -> str:
    """The hash of what the voice stage reads: each narrated shot's label,
    words and delivery marks. A draft of another script is stale."""
    rows = []
    for section in sections:
        for shot in section.shots:
            text = " ".join(shot.narration.split())
            if not text:
                continue
            marks = shot_marks(shot)
            rows.append([shot.label, text, marks["emphasis"], marks["pauses"],
                         [{"term": r.get("term", ""), "say": r.get("say", "")}
                          for r in marks["pronounce"]]])
    rows.sort(key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sentence_marks(parts: list[str], marks: dict) -> list[dict]:
    """A shot's marks shared out over its sentences: each emphasis and pause
    to the sentence that says its word, walking in order as the line does;
    a pronouncer to every sentence that says its term."""
    keys = [mark_tokens(p) for p in parts]
    out = [{"emphasis": [], "pauses": [], "pronounce": []} for _ in parts]
    for name in ("emphasis", "pauses"):
        at_part, at_token = 0, 0
        for word in (marks or {}).get(name) or []:
            hit = None
            for i in range(at_part, len(parts)):
                found = mark_at(word, keys[i], at_token if i == at_part else 0)
                if found >= 0:
                    hit = (i, found)
                    break
            if hit is None:
                hit = next(((i, mark_at(word, keys[i])) for i in range(len(parts))
                            if mark_at(word, keys[i]) >= 0), None)
                if hit is None:
                    continue
            out[hit[0]][name].append(word)
            at_part, at_token = hit[0], hit[1] + 1
    for row in (marks or {}).get("pronounce") or []:
        for i in range(len(parts)):
            if mark_at(str(row.get("term") or ""), keys[i]) >= 0:
                out[i]["pronounce"].append(dict(row))
    return out


def draft_findings(rows: list[dict], by_section: dict | None = None) -> list[dict]:
    """What the free read shows: a sentence over DRAFT_SENTENCE_LIMIT
    seconds fails; a numeral said again in a later sentence warns (the
    corpus fails it, but a callback repeats a number on purpose and the
    draft is not calibrated); each section's drift is info."""
    found = []
    seen: dict[str, str] = {}
    for row in rows:
        seconds = float(row.get("seconds") or 0.0)
        if seconds > DRAFT_SENTENCE_LIMIT:
            found.append({"severity": "fail", "rule": "direction 29",
                          "strength": "practitioner", "label": row.get("label", ""),
                          "section": row.get("section", ""), "seconds": seconds,
                          "detail": f"one sentence reads for {seconds:.1f}s, over "
                                    f"{DRAFT_SENTENCE_LIMIT:.0f}s: "
                                    f"{str(row.get('text', ''))[:80]}"})
        said = list(dict.fromkeys(_DRAFT_NUMERAL.findall(str(row.get("text") or ""))))
        for number in said:
            if number in seen:
                found.append({"severity": "warn", "rule": "direction 29",
                              "strength": "practitioner", "label": row.get("label", ""),
                              "section": row.get("section", ""),
                              "detail": f"{number} is said again (first in "
                                        f"{seen[number]}); keep it only as a callback"})
        for number in said:
            seen.setdefault(number, str(row.get("label", "")))
    for heading, row in (by_section or {}).items():
        found.append({"severity": "info", "rule": "direction 29", "section": heading,
                      "spoken": row.get("spoken"), "planned": row.get("planned"),
                      "drift": row.get("drift"),
                      "detail": f"read aloud {row.get('spoken')}s against "
                                f"{row.get('planned')}s planned"})
    return found


def draft_engine(wanted: str) -> tuple[str, str]:
    """The free reader --draft uses, or why there is none: (engine, error)."""
    if wanted == "elevenlabs":
        return "", "--draft never pays: it reads with kokoro or espeak-ng"
    if wanted == "stub":
        return "", "--draft reads aloud, and silence is not a read: use kokoro or espeak"
    if wanted in DRAFT_READERS:
        ok, hint = ENGINES[wanted].available()
        return (wanted, "") if ok else ("", f"--draft: {wanted} is not usable here: {hint}")
    if wanted != "auto":
        return "", f"--draft reads with kokoro or espeak-ng, not {wanted}"
    for name in DRAFT_READERS:
        if ENGINES[name].available()[0]:
            return name, ""
    return "", "--draft needs kokoro or espeak-ng; neither is available"


def draft_gate(path: Path, sha: str, *, accept: bool) -> dict:
    """voice.json's `draft`: is there a read-aloud draft of this script, and
    did it fail? current|stale|missing, or accepted when --accept-draft
    lets a current draft's fails through."""
    data = read_json(path, warn=False) if Path(path).exists() else None
    if not isinstance(data, dict):
        return {"status": "missing", "fails": 0}
    fails = sum(1 for f in data.get("findings") or []
                if isinstance(f, dict) and f.get("severity") == "fail")
    if data.get("script_sha") != sha:
        return {"status": "stale", "fails": fails}
    if fails and accept:
        return {"status": "accepted", "fails": fails}
    return {"status": "current", "fails": fails}


def run_draft(args, idea: Idea, project, sections: list[Section]) -> int:
    """`yt voice --draft`: every sentence read by a free local engine, its
    trimmed length measured, into voice-draft/ and voice-draft.json. No
    casting, no model, nothing paid."""
    engine, error = draft_engine(args.engine)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if not any(sh.narration.strip() for sec in sections for sh in sec.shots):
        print("error: --draft: the screenplay has no narration to read", file=sys.stderr)
        return 1
    lang = engine_lang(engine, args.lang)
    voice_id = args.voice or DEFAULT_VOICES.get(engine, {}).get(lang or "", "")
    out_dir = project.root / DRAFT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    synth = SYNTH[engine]
    rows: list[dict] = []
    by_section: dict[str, dict] = {}
    for section in sections:
        for shot in section.shots:
            text = " ".join(shot.narration.split())
            if not text:
                continue
            totals = by_section.setdefault(section.heading,
                                           {"planned": 0.0, "spoken": 0.0, "sentences": 0})
            totals["planned"] += shot.seconds
            parts = split_sentences(text)
            shared = sentence_marks(parts, shot_marks(shot))
            for k, (part, marks) in enumerate(zip(parts, shared), start=1):
                spoken, _, _ = render_marks(part, marks, engine)
                target = out_dir / f"{shot.n:03d}-{shot.label.replace('.', '-')}-{k}.wav"
                synth(spoken or part, target, voice=voice_id, rate=args.rate,
                      opts={"wpm": args.wpm, "speed": 1.0, "lang": lang,
                            "lang_id": args.lang or None})
                frames, rate, channels = read_wav(target)
                frames = trim_silence(frames, rate)
                write_wav(target, frames, rate, channels)
                seconds = round(len(frames) / 2 / channels / rate, 3)
                words = len(part.split())
                rows.append({"label": shot.label, "section": section.heading,
                             "text": part, "file": target.name, "seconds": seconds,
                             "words": words,
                             "wpm": round(words / seconds * 60, 1) if seconds else 0.0})
                totals["spoken"] += seconds
                totals["sentences"] += 1
    for row in by_section.values():
        row["drift"] = round(row["spoken"] - row["planned"], 2)
        row["planned"], row["spoken"] = round(row["planned"], 2), round(row["spoken"], 2)
    findings = draft_findings(rows, by_section)
    data = {"idea": {"title": idea.title}, "engine": engine, "voice": voice_id,
            "script_sha": script_sha(sections), "sentences": rows,
            "by_section": by_section, "findings": findings,
            "listened": "not evidenced"}
    path = write_json(project.root / DRAFT_FILE, data)
    print(f"draft: {len(rows)} sentence(s) read with {engine} · "
          f"{sum(r['seconds'] for r in rows):.1f}s against "
          f"{sum(r['planned'] for r in by_section.values()):.1f}s planned")
    for finding in findings:
        if finding["severity"] in ("fail", "warn"):
            print(f"{finding['severity']}: {finding.get('label', '')} {finding['detail']}",
                  file=sys.stderr)
    print(f"wrote {path} (no stage reads it as the voice; heard by no one yet)",
          file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "voice", parents=list(parents),
        help="record the narration, measure it and caption it",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--engine", default="auto",
                   choices=["auto", *ENGINES], help="which synthesiser (default auto)")
    p.add_argument("--voice", default="", help="voice id or name for the engine")
    p.add_argument("--lang", default="",
                   help="language code the engine wants (eleven: pt; kokoro: p)")
    p.add_argument("--reference", metavar="WAV",
                   help="a short recording to clone, for chatterbox")
    p.add_argument("--script", metavar="FILE",
                   help="the screenplay (default: the project's script.json)")
    p.add_argument("--layout", default="sequential",
                   choices=["sequential", "planned"],
                   help="place takes back to back (default) or at the script's "
                        "own timecodes")
    p.add_argument("--gap", type=float, default=GAP,
                   help=f"seconds between shots (default {GAP})")
    p.add_argument("--section-gap", type=float, default=SECTION_GAP,
                   help=f"seconds between sections (default {SECTION_GAP})")
    p.add_argument("--rate", type=int, default=24000,
                   help="sample rate to ask the engine for (default 24000)")
    p.add_argument("--format", dest="audio_format", metavar="FMT",
                   help="hosted-engine output format (default pcm_<rate>, "
                        "wrapped as WAV here). mp3_44100_128 and the like are "
                        "kept as sent: smaller on disk, but no level matching "
                        "and no --stitch without ffmpeg")
    p.add_argument("--wpm", type=int, default=165, help="espeak speaking rate")
    p.add_argument("--engine-model", dest="engine_model",
                   help="the synthesiser's own model id, where it has one "
                        "(--model stays the Claude model)")
    p.add_argument("--max-chars", type=int,
                   help="refuse to send more characters than this (elevenlabs)")
    p.add_argument("--no-normalise", action="store_true", dest="no_normalise",
                   help="leave each take's level as the engine made it")
    p.add_argument("--no-trim", action="store_true", dest="no_trim",
                   help="keep the silence at each take's edges")
    p.add_argument("--stitch", action="store_true",
                   help="also write one continuous voice.wav")
    p.add_argument("--redo", default="", metavar="LABELS",
                   help="comma-separated shot labels to record again, or 'all'")
    p.add_argument("--fresh", action="store_true",
                   help="cast again even if the screenplay did not change")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="count characters and estimate, synthesise nothing")
    p.add_argument("--draft", action="store_true",
                   help="read the screenplay aloud, sentence by sentence, with a "
                        "free local engine (kokoro, else espeak-ng) into "
                        "voice-draft/ and voice-draft.json (direction 29); no "
                        "casting, no model, nothing paid")
    p.add_argument("--accept-draft", action="store_true", dest="accept_draft",
                   help="record a paid voice although the current read-aloud "
                        "draft has a fail")
    p.add_argument("--list-engines", action="store_true",
                   help="print the engines and whether each one is usable here")
    p.set_defaults(run=run)
    return p


def run_wordless(args, idea, project, sections) -> int:
    """No narration: the film carries descriptive captions instead of a voice."""
    if args.dry_run:
        print("wordless film: no narration to record; `yt voice` writes "
              "descriptive captions", file=sys.stderr)
        return 0
    rules, digest = house.for_args(args, project, "voice")
    director = Harness(project.root, "voice", model=args.model, role=WORDLESS_ROLE,
                       house=rules, house_digest=digest,
                       fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    sound = read_json(project.source("sound"), warn=False) or {}
    try:
        data, cues, notes = wordless(idea, sections, director, sound)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if not cues:
        print("error: no descriptive caption survived the gate's limits; the gate "
              "needs at least one cue", file=sys.stderr)
        return 1
    vtt = project.out("captions")
    write_cues(Path(vtt), cues)
    write_cues(project.root / "narration.srt", cues, srt=True)
    data["director"] = director.report()
    data["usage"] = spend.from_report(data["director"])
    manifest = project.out("voice")
    write_json(manifest, data)
    print(f"wordless film: {len(cues)} descriptive caption(s), voice ends at "
          f"{data['voice_end']:g}s (the last shot's start)")
    for path in (manifest, vtt, str(project.root / "narration.srt")):
        print(f"wrote {path}", file=sys.stderr)
    return 0


def run(args) -> int:
    if args.list_engines:
        chosen = pick_engine("auto")
        for name, engine in ENGINES.items():
            ok, hint = engine.available()
            mark = "ok " if ok else "-  "
            note = "" if ok else f"  ({hint})"
            star = " <- auto" if name == chosen else ""
            print(f"{mark}{name:<11} {engine.describe}{note}{star}")
        return 0

    idea, project = workspace.resolve(args, inputs=("script",))
    script_file = project.source("script", args.script)
    if not script_file:
        print("error: no screenplay; run `yt script` first, or pass --script FILE",
              file=sys.stderr)
        return 1
    sections, script = load_script(script_file)
    # The screenplay recorded which language the film is in; follow it unless
    # told otherwise, so the voice cannot drift from the words.
    if not args.lang and script.get("lang"):
        args.lang = str(script["lang"])
        if not args.quiet:
            print(f"language {args.lang} from the screenplay", file=sys.stderr)
    if getattr(args, "draft", False):
        return run_draft(args, idea, project, sections)
    takes_planned = sum(1 for s in sections for sh in s.shots if sh.narration.strip())
    if not takes_planned:
        return run_wordless(args, idea, project, sections)

    engine = pick_engine(args.engine)
    ok, hint = ENGINES[engine].available()
    if not ok:
        print(f"error: engine {engine!r} is not usable here: {hint}",
              file=sys.stderr)
        return 1
    if engine == "chatterbox" and not args.reference:
        print("warning: chatterbox clones a reference; without --reference you "
              "get its default voice", file=sys.stderr)

    fmt = args.audio_format
    if fmt and not fmt.startswith(FORMAT_PREFIXES):
        print(f"error: --format {fmt!r} is not one the API takes; it starts "
              f"with one of {', '.join(FORMAT_PREFIXES)}", file=sys.stderr)
        return 1
    if fmt and not fmt.startswith("pcm_") and args.stitch:
        print(f"warning: --stitch needs WAV takes; {fmt} is kept as sent, so "
              "the stitched track will be empty. Use a pcm_ format.",
              file=sys.stderr)

    opts = {"wpm": args.wpm, "reference": args.reference, "model": args.engine_model,
            "max_chars": args.max_chars, "lang_id": args.lang or None,
            "format": fmt or None}

    if args.dry_run:
        rows = VoiceOver(idea, sections, project.root / "voice", engine=engine,
                         voice=args.voice, lang=args.lang, opts=opts,
                         verbose=False).takes()
        chars = sum(t.chars for t in rows)
        print(f"{len(rows)} takes · {chars} characters{spoken_note(rows)} · engine {engine}")
        print(f"estimated speech at 14 chars/s: "
              f"{timecode(round(chars / 14))} against "
              f"{timecode(sum(t.planned_seconds for t in rows))} planned")
        if engine == "elevenlabs":
            print("ElevenLabs bills per character; the kit's budget check runs "
                  "before each call.")
        print("(--dry-run: nothing cast, nothing recorded)", file=sys.stderr)
        return 0

    # Direction 29: a paid voice is recorded only after a free read of this
    # script. Local engines are free and are not gated.
    draft = None
    if not ENGINES[engine].local:
        draft = draft_gate(project.root / DRAFT_FILE, script_sha(sections),
                           accept=getattr(args, "accept_draft", False))
        if draft["status"] in ("missing", "stale"):
            print(f"warning: {'no' if draft['status'] == 'missing' else 'a stale'} "
                  "read-aloud draft of this screenplay; direction rule 29 asks for "
                  "a free take before any voice is paid for -- run `yt voice "
                  "--draft` first", file=sys.stderr)
        elif draft["status"] == "current" and draft["fails"]:
            print(f"error: the read-aloud draft of this screenplay has "
                  f"{draft['fails']} fail(s) (direction rule 29; see {DRAFT_FILE}). "
                  "Fix the script and run `yt voice --draft` again, or pass "
                  "--accept-draft to pay for the voice anyway", file=sys.stderr)
            return 1

    # Casting and direction are the model's; --voice is the operator's veto.
    rules, digest = house.for_args(args, project, "voice")
    director = Harness(project.root, "voice", model=args.model, role=ROLE,
                       house=rules, house_digest=digest,
                       fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    offered = candidates(engine, engine_lang(engine, args.lang))
    styles = read_json(project.source("styles"), warn=False) or {}
    # A screenplay with no marks was written before them (or by a writer
    # that gave none): it is asked today's casting and retake questions, so
    # a project voiced before AUTO-139 keeps its kept answers and its bill.
    marked = has_marks(sections)
    try:
        cast = director.decide("casting", "\n\n".join([
            cast_instruction(engine, args.lang, marked=marked),
            brief("Film", {"title": idea.title, "premise": idea.description,
                           "genre": idea.genre,
                           "treatment": (styles.get("styles") or [{}])[0].get("name", "")
                           if isinstance(styles.get("styles"), list) else ""}),
            brief("Candidates", offered),
            brief("Sections", [{"heading": sec.heading, "purpose": sec.purpose,
                                "opening_line": next((sh.narration for sh in sec.shots
                                                      if sh.narration.strip()), "")[:200],
                                **({"notes": [sh.note for sh in sec.shots if sh.note][:8]}
                                   if marked else {})}
                               for sec in sections])]),
            CAST_SCHEMA if marked else CAST_SCHEMA_PRE_MARKS)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    voice_id, cast_opts = apply_cast(cast, offered, engine=engine, forced=args.voice)
    opts |= cast_opts
    try:
        require_voice(engine, voice_id, offered)
    except CastingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"cast: {voice_id or 'engine default'} at {opts['speed']}x -- "
              f"{str(cast.get('why', ''))[:120]}", file=sys.stderr)
    if not marked:
        cast = notes_only(cast)
    by_heading, unmatched = delivery_from(cast, sections)
    if unmatched:
        print(f"warning: {len(unmatched)} delivery note(s) name no section and "
              f"reach no take: {', '.join(r['section'] or '?' for r in unmatched[:4])}",
              file=sys.stderr)
    recorder = VoiceOver(
        idea, sections, project.root / "voice", engine=engine, voice=voice_id,
        lang=args.lang, rate=args.rate, opts=opts, layout=args.layout,
        gap=args.gap, section_gap=args.section_gap,
        normalise=not args.no_normalise, trim=not args.no_trim,
        redo=set(p.strip() for p in args.redo.split(",") if p.strip()),
        directions=directions_from(
            ((read_json(project.out("voice"), warn=False) or {})
             .get("review") or {}).get("retakes")),
        delivery=by_heading, verbose=not args.quiet)

    try:
        data = recorder.run()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    known = {f.name for f in fields(Take)}
    takes = [Take(**{k: v for k, v in row.items() if k in known and k != "words"},
                  words=[Word(**w) for w in row.get("words", [])])
             for row in data["takes"]]
    data["casting"] = {"voice": voice_id, "speed": opts["speed"],
                       "settings": opts.get("settings"),
                       "delivery": casting_delivery(cast), "why": cast.get("why", "")}
    data["delivery"] = delivery_summary(takes, unmatched)
    if draft is not None:
        data["draft"] = draft
    print_voice(data, takes)

    # The director hears the measurements and names what to record again.
    try:
        review = director.decide("retakes", "\n\n".join([
            "The read is recorded and measured. Name the takes to record again, "
            "with the reason, or say the read stands.",
            brief("Per take (cps = characters per second; the caption gate rejects "
                  f"over {CPS_LIMIT:.0f})",
                  [{"label": t.label, "section": t.section, "seconds": t.seconds,
                    "planned": t.planned_seconds, "cps": round(t.cps, 1),
                    "text": t.text[:160],
                    **{k: t.delivery[k] for k in ("emphasis", "pauses", "pronounce", "section_note")
                       if marked and (t.delivery or {}).get(k)}} for t in takes]),
            brief("Drift by section", data["by_section"]),
            brief("Overlaps", data["overlaps"])]), RETAKE_SCHEMA)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    data["review"] = reviewed(review, data["takes"])
    data["director"] = director.report()      # "engine" stays the TTS engine
    data["usage"] = spend.from_report(data["director"])
    if review.get("retakes"):
        print(f"\n  the director asks for {len(review['retakes'])} retake(s):")
        for row in review["retakes"][:10]:
            pace = f" @{row['speed']}x" if row.get("speed") else ""
            print(f"    {row.get('label', ''):<6}{pace} {str(row.get('reason', ''))[:80]}")
        print("    record them with: yt voice --redo "
              + ",".join(str(r.get("label", "")) for r in review["retakes"]))
    if review.get("notes"):
        print(f"  director's notes: {str(review['notes'])[:300]}")

    vtt = project.out("captions")
    film_end = max((t.at + t.seconds for t in takes), default=None)
    clamped, too_short = write_captions(Path(vtt), takes, film_end=film_end)
    write_captions(project.root / "narration.srt", takes, srt=True, film_end=film_end)
    if data["overlaps"]:
        worst = max(data["overlaps"], key=lambda r: r["by"])
        print(f"warning: {len(data['overlaps'])} take(s) overlap the next one "
              f"on this layout (worst: {worst['label']} runs {worst['by']}s "
              f"into {worst['next']}). Captions were clamped; the audio still "
              "overlaps -- use --layout sequential, or re-time the script.",
              file=sys.stderr)
    if too_short:
        print(f"warning: {too_short} caption(s) under 0.7s, which delivery flags",
              file=sys.stderr)
    manifest = project.out("voice")
    write_json(manifest, data)
    written = [manifest, vtt, str(project.root / "narration.srt")]
    if args.stitch:
        track = project.root / "voice.wav"
        total, skipped = stitch(track, takes, project.root / "voice")
        written.append(f"{track} ({total:.1f}s)")
        if skipped:
            print(f"warning: {len(skipped)} take(s) left out of voice.wav, "
                  f"needing a resample: {', '.join(skipped[:4])}",
                  file=sys.stderr)
    for path in written:
        print(f"wrote {path}", file=sys.stderr)

    if "estimated" in data["timing"]:
        print("note: word timings are estimated from text length, not aligned. "
              "The captions cut per shot and are safe; word-level karaoke needs "
              "an engine that reports timings (elevenlabs) or a forced "
              "alignment pass.", file=sys.stderr)
    print(f"\ntotal drift {data['drift_seconds']:+.1f}s against "
          f"{timecode(data['planned_seconds'])} planned", file=sys.stderr)
    return 0
