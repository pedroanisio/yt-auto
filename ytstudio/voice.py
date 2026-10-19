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
import shutil
import struct
import subprocess
import sys
import textwrap
import time
import wave
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import house, workspace
from .harness import Harness, HarnessError, brief
from .core import Idea, read_json, timecode, write_json
from .script import Section, load_script

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
                               "properties": {"section": {"type": "string"},
                                              "note": {"type": "string"}},
                               "required": ["section", "note"]}},
        "why": {"type": "string"},
    },
    "required": ["voice", "speed", "why"],
}

RETAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "retakes": {"type": "array",
                    "items": {"type": "object",
                              "properties": {"label": {"type": "string"},
                                             "reason": {"type": "string"}},
                              "required": ["label", "reason"]}},
        "verdict": {"type": "string", "enum": ["ok", "retake"]},
        "notes": {"type": "string"},
    },
    "required": ["retakes", "verdict", "notes"],
}


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


def cast_instruction(engine: str, lang: str) -> str:
    """The first paragraph of the casting brief: what `voice` may be."""
    default = ("the engine has no default: `voice` must be one of the candidate ids"
               if engine not in ENGINES_WITH_DEFAULT else
               "empty keeps the engine's default")
    return (f"Cast the narrator for this film and set the read. Engine: {engine}; "
            f"language: {lang or 'unspecified'}. Choose `voice` from the "
            f"candidates ({default}), `speed` within {SPEED_RANGE}, and one "
            "delivery note per section.")


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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    block = data.get("alignment") or data.get("normalized_alignment") or {}
    chars = block.get("characters") or []
    starts = block.get("character_start_times_seconds") or []
    ends = block.get("character_end_times_seconds") or []
    if not (chars and len(chars) == len(starts) == len(ends)):
        return []
    out: list[Word] = []
    buf, first, last = "", None, None
    for ch, a, b in zip(chars, starts, ends):
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
    cmd = ["espeak-ng", "-v", voice or "en-us", "-s", str(opts.get("wpm", 165)),
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
                out.append(Take(n=shot.n, label=shot.label,
                                section=section.heading, text=text,
                                planned_at=shot.at,
                                planned_seconds=shot.seconds,
                                chars=len(text)))
        return out

    def _fingerprint(self, take: Take) -> str:
        parts = [self.engine, self.voice, self.lang, str(self.rate),
                 json.dumps(self.opts, sort_keys=True, default=str), take.text]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    # -- recording ---------------------------------------------------------

    def _record_local(self, takes: list[Take]) -> None:
        synth = SYNTH[self.engine]
        for take in takes:
            target = self.dir / f"{take.n:03d}-{take.label.replace('.', '-')}.wav"
            cached = self.cache / f"{self._fingerprint(take)}.wav"
            if cached.exists() and take.label not in self.redo:
                take.cached = True
            else:
                self._say(f"  {take.label} {len(take.text):>4} chars")
                synth(take.text, cached, voice=self.voice, rate=self.rate,
                      opts=self.opts | {"lang": self.engine_lang})
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
        scenes = [{"id": f"{t.n:03d}-{t.label.replace('.', '-')}", "text": t.text}
                  for t in takes]
        scenes_file = self.dir / "scenes.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        scenes_file.write_text(json.dumps(scenes, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        fmt = self.opts.get("format") or f"pcm_{self.rate}"
        # tts_scenes carries each shot's neighbours and request ids, so the
        # prosody runs across the cuts instead of resetting every shot.
        call = lambda: kit.tts_scenes(  # noqa: E731
            client, str(scenes_file), self.voice, str(self.dir),
            model=self.opts.get("model") or kit.DEFAULT_MODEL,
            fmt=fmt, language=self.lang or None, timestamps=True,
            settings=self.opts.get("settings") or None,
            max_chars=self.opts.get("max_chars"),
            force=bool(self.redo), log=self._say)
        self._retrying(call)
        for take, scene in zip(takes, scenes):
            raw = self.dir / f"{scene['id']}.mp3"
            take.engine, take.voice = self.engine, self.voice
            take.words = words_from_alignment(
                self.dir / f"{scene['id']}.alignment.json")
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

    def run(self) -> dict:
        takes = self.takes()
        if not takes:
            raise RuntimeError("no narration in the screenplay to record")
        self._say(f"[voice] {self.engine}"
                  + (f" · {self.voice}" if self.voice else "")
                  + f" · {len(takes)} takes · {sum(t.chars for t in takes)} chars")
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

def caption_lines(text: str, width: int = 42) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [text]


def _stamp(seconds: float, *, comma: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_captions(path: Path, takes: list[Take], *,
                   srt: bool = False) -> tuple[int, int]:
    """One cue per shot, on the voice's own timeline.

    A cue is clamped so it never runs into the next one: overlapping cues are
    invalid in both formats, and the planned layout produces them whenever a
    take came out longer than the script budgeted. Returns how many were
    clamped and how many ended up under the 0.7 s the delivery gate wants.
    """
    rows = sorted(takes, key=lambda t: t.at)
    clamped = too_short = 0
    out = [] if srt else ["WEBVTT", ""]
    for i, take in enumerate(rows, 1):
        start, end = take.at, take.at + take.seconds
        nxt = rows[i].at if i < len(rows) else None
        if nxt is not None and end > nxt:
            end, clamped = max(start + 0.05, nxt - 0.001), clamped + 1
        if end - start < 0.7:
            too_short += 1
        if srt:
            out.append(str(i))
        out.append(f"{_stamp(start, comma=srt)} --> {_stamp(end, comma=srt)}")
        out += caption_lines(take.text)
        out.append("")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return clamped, too_short


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
          + f" · {len(takes)} takes · timing: {', '.join(data['timing'])}")
    print(f"spoken {timecode(data['spoken_seconds'])} · "
          f"planned {timecode(data['planned_seconds'])} · "
          f"drift {data['drift_seconds']:+.1f}s · "
          f"voice ends {data['voice_end']:.1f}s")

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
    p.add_argument("--list-engines", action="store_true",
                   help="print the engines and whether each one is usable here")
    p.set_defaults(run=run)
    return p


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
    takes_planned = sum(1 for s in sections for sh in s.shots if sh.narration.strip())
    if not takes_planned:
        print("error: the screenplay has no narration to record", file=sys.stderr)
        return 1

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
        print(f"{len(rows)} takes · {chars} characters · engine {engine}")
        print(f"estimated speech at 14 chars/s: "
              f"{timecode(round(chars / 14))} against "
              f"{timecode(sum(t.planned_seconds for t in rows))} planned")
        if engine == "elevenlabs":
            print("ElevenLabs bills per character; the kit's budget check runs "
                  "before each call.")
        print("(--dry-run: nothing cast, nothing recorded)", file=sys.stderr)
        return 0

    # Casting and direction are the model's; --voice is the operator's veto.
    rules, digest = house.for_args(args, project, "voice")
    director = Harness(project.root, "voice", model=args.model, role=ROLE,
                       house=rules, house_digest=digest,
                       fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    offered = candidates(engine, engine_lang(engine, args.lang))
    styles = read_json(project.source("styles"), warn=False) or {}
    try:
        cast = director.decide("casting", "\n\n".join([
            cast_instruction(engine, args.lang),
            brief("Film", {"title": idea.title, "premise": idea.description,
                           "genre": idea.genre,
                           "treatment": (styles.get("styles") or [{}])[0].get("name", "")
                           if isinstance(styles.get("styles"), list) else ""}),
            brief("Candidates", offered),
            brief("Sections", [{"heading": sec.heading, "purpose": sec.purpose,
                                "opening_line": next((sh.narration for sh in sec.shots
                                                      if sh.narration.strip()), "")[:200]}
                               for sec in sections])]), CAST_SCHEMA)
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
    recorder = VoiceOver(
        idea, sections, project.root / "voice", engine=engine, voice=voice_id,
        lang=args.lang, rate=args.rate, opts=opts, layout=args.layout,
        gap=args.gap, section_gap=args.section_gap,
        normalise=not args.no_normalise, trim=not args.no_trim,
        redo=set(p.strip() for p in args.redo.split(",") if p.strip()),
        verbose=not args.quiet)

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
                       "delivery": cast.get("delivery", []), "why": cast.get("why", "")}
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
                    "text": t.text[:160]} for t in takes]),
            brief("Drift by section", data["by_section"]),
            brief("Overlaps", data["overlaps"])]), RETAKE_SCHEMA)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    data["review"] = review
    data["director"] = director.report()      # "engine" stays the TTS engine
    if review.get("retakes"):
        print(f"\n  the director asks for {len(review['retakes'])} retake(s):")
        for row in review["retakes"][:10]:
            print(f"    {row.get('label', ''):<6} {str(row.get('reason', ''))[:80]}")
        print("    record them with: yt voice --redo "
              + ",".join(str(r.get("label", "")) for r in review["retakes"]))
    if review.get("notes"):
        print(f"  director's notes: {str(review['notes'])[:300]}")

    vtt = project.out("captions")
    clamped, too_short = write_captions(Path(vtt), takes)
    write_captions(project.root / "narration.srt", takes, srt=True)
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
