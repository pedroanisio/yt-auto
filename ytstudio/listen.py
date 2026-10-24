"""yt qa --listen: a model that hears the film's rendered audio (AUTO-126).

QA judges stills. voiceover, sound_design and music_score are scored only
from a listening review that heard what the film carries (AUTO-105), and
the two the pipeline had heard the cue sheet and the takes, never the mix.
`--listen` is the opt-in pass that hears the final track:

1. Preflight, before a frame is rendered or a pass directory is made: the
   gateway is on (YT_GATEWAY=vercel) and its engine ready, the model is one
   of models.LISTEN_MODELS with a provider verified to take audio, it is
   from another maker than every author of the script, the cue sheet and
   the voice, the film exists and is not older than its scene, the build
   has an audio track, ffmpeg and ffprobe are on PATH, a `--reuse`d pass
   was drawn from this scene, the track fits LISTEN_MAX_MB, the model has
   a price, and the estimate fits `--listen-budget-usd`. Each failure is
   named; nothing is replaced by something else.
2. The film's own audio stream, taken out by stream copy (`-c copy`, ADTS),
   to `qa/listen/<film sha256[:16]>.aac`, reused while the film is the same;
   its length from ffprobe.
3. One decision through the gateway engine with the track as media, cached
   in decisions/qa/ by the audio's bytes, so the same mix asks nothing.
4. The answer checked past the schema: each finding's owner and severity,
   and its timestamp inside the audio. A finding that fails is asked once
   more with the problem named, then dropped and named under not_evidenced.
5. The record, `qa/<pass>/listening.json`, filed as the current listening
   evidence (`evidence_row`) that AUTO-105's rule reads, its findings merged
   into the scorecard as the listener's, its cost merged into the usage.
6. The transcript (AUTO-152), for a narrated film: one more decision on the
   same harness and track, `transcript-<audio sha256[:16]>`, asking each
   voiced shot's words and times by label and place alone, never the words
   it should hear. Each shot is held to its narration as build.json placed
   it (`normalise_words`, `wer`); one over INTELLIGIBILITY_WER is a minor
   voiceover finding owned by voice at the shot's time, and one whose
   answer fails `transcript_check` is asked once more, then not evidenced.

The listener's criteria are docs/production.md §2 rows 6-8, word for word
(LISTEN_CRITERIA, pinned against the doc by test_doc_production).
"""

from __future__ import annotations

import difflib
import hashlib
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gateway, models
from .core import read_json, write_json
from .flags import own_reviewer
from .harness import Harness, HarnessError, brief

#: The listener when --listen-model is not given, and the most a listen may
#: cost (its one ask-again included) before it is refused.
LISTEN_MODEL = "google/gemini-3.6-flash"
LISTEN_BUDGET_USD = 0.50

#: The largest track sent, in MB (the request carries it inline, base64,
#: so 14 MB is about 19 MB on the wire). TO VERIFY live: the gateway's and
#: the provider's request limits.
LISTEN_MAX_MB = 14.0
#: Audio tokens per second of sound, for the estimate (Gemini's documented
#: rate). TO VERIFY live against a call's usage.
LISTEN_TOKENS_PER_SECOND = 32
#: The answer's tokens, for the estimate: three categories, their findings.
LISTEN_OUTPUT_TOKENS = 4000
#: The length an estimate assumes when no build says how long the film is.
ASSUMED_SECONDS = 600

#: The transcript's answer, for the estimate: every voiced shot's words and
#: times (AUTO-152). TO VERIFY live: that a ten-minute film's fits.
TRANSCRIPT_OUTPUT_TOKENS = 8000
#: The transcript question's characters, for an estimate made before it is
#: written (spend's `--listen` increment).
TRANSCRIPT_PROMPT_CHARS = 4000
#: The word error rate over which a shot's narration is filed as not heard as
#: written. NO STANDARD: provisional, until a kokoro and an ElevenLabs film
#: are measured against it.
INTELLIGIBILITY_WER = 0.15
#: How far a shot's heard speech may start outside its place on the film, and
#: end past the track, in seconds.
TRANSCRIPT_TIME_SLACK_S = 1.0

#: Where the extracted tracks live, under the project's qa/, and the record
#: each listening pass leaves in its pass directory.
LISTEN_DIR = "listen"
LISTENING = "listening.json"
#: The container the stream is copied into: ADTS holds an AAC stream as it
#: is, with no re-encode. TO VERIFY live: that the listener takes audio/aac
#: (else MP4 audio, `-f ipod` to .m4a, also a stream copy).
CONTAINER, SUFFIX = "adts", ".aac"

#: docs/production.md §2 rows 6-8: what each audio category measures.
LISTEN_CRITERIA = {
    "voiceover": ("Voiceover", "intelligibility, naturalness, emphasis, sync"),
    "sound_design": ("Sound design", "effects, ambience, impacts, spatial balance, dynamics"),
    "music_score": ("Music / score", "progression, emotional fit, transitions, repetition"),
}

#: Who can own a heard finding: the stages that make what is heard (the
#: takes, the cues, the mix, the encode), and a human route for what none
#: of them can fix. All are qa.OWNERS, so improve routes every one.
LISTEN_OWNERS = ("voice", "sound", "build", "render", "external")
SEVERITIES = ("blocking", "minor")

LISTEN_ROLE = (
    "You are the listening reviewer of docs/production.md: you hear the whole "
    "soundtrack of a rendered film, as its viewer will, and judge it by "
    "professional standards against these rows of the publication bar:\n"
    + "\n".join(f"- {name}: {words}" for name, words in LISTEN_CRITERIA.values())
    + "\nYou hear; you do not see. Every finding is something audible, at its "
    "time in seconds from the start of the audio: what is heard, why it lowers "
    "quality, the root cause in the owning stage's source, the owner and the "
    "fix. You file no score. Whatever the audio alone cannot settle (whether "
    "the voice is in sync with the picture, what the frames show) you list "
    "under not_heard and never guess. When the picture's timeline as built is "
    "listed, judge whether each hit or music entry is audibly at the listed time "
    "of its anchored word or cut, and file one that lands early or late as a "
    "finding at its time."
)

_FINDING = {
    "type": "object",
    "properties": {"timestamp": {"type": "number"}, "problem": {"type": "string"},
                   "why": {"type": "string"}, "root_cause": {"type": "string"},
                   "owner": {"type": "string", "enum": list(LISTEN_OWNERS)},
                   "fix": {"type": "string"},
                   "severity": {"type": "string", "enum": list(SEVERITIES)}},
    "required": ["timestamp", "problem", "why", "root_cause", "owner", "fix", "severity"],
}
_CATEGORY = {
    "type": "object",
    "properties": {"heard": {"type": "string"},
                   "findings": {"type": "array", "items": _FINDING}},
    "required": ["heard", "findings"],
}
#: What the listener answers: what it heard, per category what it heard and
#: its findings, and what it could not hear. No score: the scorecard's
#: reviewer scores, from this.
LISTEN_SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "object",
                  "properties": {"seconds": {"type": "number"},
                                 "narration": {"type": "boolean"},
                                 "music": {"type": "boolean"},
                                 "effects": {"type": "boolean"}},
                  "required": ["seconds", "narration", "music", "effects"]},
        "categories": {"type": "object",
                       "properties": {k: _CATEGORY for k in LISTEN_CRITERIA},
                       "required": list(LISTEN_CRITERIA)},
        "not_heard": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["heard", "categories", "not_heard"],
}

#: What the transcript answers (AUTO-152): per shot asked, the words heard and
#: where on the audio they were said.
TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {"shots": {"type": "array", "items": {
        "type": "object",
        "properties": {"label": {"type": "string"}, "start": {"type": "number"},
                       "end": {"type": "number"}, "words": {"type": "string"}},
        "required": ["label", "start", "end", "words"]}}},
    "required": ["shots"],
}

#: What the scorecard's reviewer is told when a current listen heard the
#: film (qa.ROLE_LISTENED puts it in its role).
LISTENED = (
    "One of those reviews heard the rendered film's own audio: listening.json in "
    "this pass, a model that listened to the whole soundtrack. Score voiceover, "
    "sound_design and music_score from what it heard; its findings are already "
    "filed as its own, so do not repeat them, and file none of your own on how "
    "the film sounds.")


class Refused(Exception):
    """--listen cannot run as asked; the message names every reason."""


def add_flags(p) -> None:
    """--listen, --listen-model, --listen-budget-usd on `yt qa`."""
    p.add_argument("--listen", action="store_true",
                   help="also send the film's rendered audio to a model that hears it "
                        "(through the gateway: YT_GATEWAY=vercel and its key), and file "
                        f"what it heard as the listening evidence in {LISTENING}")
    p.add_argument("--listen-model", default=LISTEN_MODEL, metavar="ID",
                   help=f"the listener, one of models.LISTEN_MODELS (default {LISTEN_MODEL})")
    p.add_argument("--listen-budget-usd", type=float, default=LISTEN_BUDGET_USD,
                   metavar="USD",
                   help="the most the listen may cost; refused before anything is paid when "
                        f"its estimate is higher (default {LISTEN_BUDGET_USD:.2f})")


# --------------------------------------------------------------------------- #
# who wrote what is heard
# --------------------------------------------------------------------------- #

def _engine_models(doc: Any) -> list[str]:
    """The models a stage record names: its engine's model and answering
    models, and its usage's."""
    out: list[str] = []
    doc = doc if isinstance(doc, dict) else {}
    for part in (doc.get("engine"), doc.get("usage")):
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict):
            if isinstance(part.get("model"), str):
                out.append(part["model"])
            out += [str(m) for m in (part.get("by_model") or {}) if m]
    return [m.strip() for m in out if m and m.strip()]


def authors(script: dict | None, sound: dict | None, voice: dict | None
            ) -> list[tuple[str, str]]:
    """(stage, model) for every model that wrote what the film says and
    plays: the screenplay's, the cue sheet's and the voice's. A voice
    engine counts only when it names a model a maker made (a gateway TTS
    model); a TTS engine's own name (`kokoro`) is not a model."""
    out: list[tuple[str, str]] = []
    for stage, doc in (("script", script), ("sound", sound), ("voice", voice)):
        for name in _engine_models(doc):
            if stage == "voice" and models.maker(name) is None:
                continue
            if (stage, name) not in out:
                out.append((stage, name))
    return out


def family(stage: str, author: str, model: str) -> str:
    """Why `model` may not hear what `author` wrote for `stage`, or "".
    flags.own_reviewer judges the pair, as it does for every reviewer; the
    words are the listen's own, since `yt qa --listen` has no --model or
    --reviewer-model to change. An author nobody can place stays refused:
    its maker may be the listener's, and nobody can show it is not."""
    if not own_reviewer(author, model):
        return ""
    made, hears = models.maker(author), models.maker(model)
    where = f"{stage}.json names {author} among the models that wrote it"
    if made is None:
        return (f"{where}, and nobody can say who made {author} (not a provider/model id, a "
                f"Claude or an OpenAI name), so --listen-model {model} cannot be shown to come "
                f"from another maker; rerun `yt {stage}` with a model whose maker is known, "
                "or leave --listen off")
    if made == hears:
        return (f"{where}, {made}'s, and --listen-model {model} is {hears}'s too: a maker's "
                "models share their training, so one does not judge another's work; pass "
                f"--listen-model with another maker's model (models.LISTEN_MODELS: "
                f"{', '.join(models.LISTEN_MODELS)})")
    return (f"{where}, of the same family as --listen-model {model}; pass --listen-model "
            "with another maker's model")


# --------------------------------------------------------------------------- #
# the estimate
# --------------------------------------------------------------------------- #

def estimate_usd(model: str, seconds: float, chars: int, *, transcript: bool = True,
                 transcript_chars: int = TRANSCRIPT_PROMPT_CHARS) -> float | None:
    """What one listen costs at most: the track's tokens and the prompt's
    in, LISTEN_OUTPUT_TOKENS out, twice (the one ask-again); and, for a
    narrated film, the transcript the same way (the same track in,
    TRANSCRIPT_OUTPUT_TOKENS out, twice). None when nobody priced the model."""
    import gateway_engine
    row = gateway_engine.price_for(model)
    if not row:
        return None

    def once(chars: float, out: int) -> float:
        tokens_in = seconds * LISTEN_TOKENS_PER_SECOND + chars / 4
        return (tokens_in * float(row.get("input", 0))
                + out * float(row.get("output", 0))) / 1_000_000
    total = 2 * once(chars, LISTEN_OUTPUT_TOKENS)
    if transcript:
        total += 2 * once(transcript_chars, TRANSCRIPT_OUTPUT_TOKENS)
    return round(total, 4)


def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def estimate_for(root: Path | None, argv=()) -> float | None:
    """The listen's estimate for the project under `root` (spend's
    `--listen` increment): the film's length from build.json, else
    ASSUMED_SECONDS, and a narration of 15 characters a second; the
    transcript too unless the build has no voiced shot or the film is
    wordless (with no build, a narrated film is assumed)."""
    argv = [str(a) for a in argv or ()]
    model = _flag(argv, "--listen-model") or LISTEN_MODEL
    build = read_json(Path(root) / "build.json", warn=False) if root is not None else None
    voice = read_json(Path(root) / "voice.json", warn=False) if root is not None else None
    narrated = not (isinstance(voice, dict) and voice.get("wordless")) and (
        bool(voiced_shots(build)) if isinstance(build, dict) else True)
    seconds = ((build or {}).get("scene") or {}).get("duration") if isinstance(build, dict) else None
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = float(ASSUMED_SECONDS)
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = float(ASSUMED_SECONDS)
    return estimate_usd(model, seconds, int(seconds * 15) + 6000, transcript=narrated)


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

@dataclass
class Prepared:
    """What preflight found and made: the listen, ready to ask."""
    model: str
    providers: list[str]
    film: dict
    audio: dict
    scene_sha256: str
    scene_path: str
    prompt: str
    system: str
    estimate_usd: float
    budget_usd: float
    #: The voiced shots the transcript asks after (`voiced_shots`), and why
    #: none is asked when there are none (a wordless film, no voiced shot).
    shots: list = field(default_factory=list)
    transcript_skip: str = ""


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _where(project) -> str:
    return "" if project.standalone else f"--project {project.root}"


def preflight(args, project, *, home: Path, scene: str, build: dict, script: dict,
              voice: dict, sound: dict, title: str, house: str = "") -> Prepared:
    """Everything --listen needs, checked before anything is rendered or
    paid; Refused names each piece that is missing. The film's audio is
    taken out here, since its size and length are part of the check."""
    from . import qa
    import gateway_engine
    model = str(getattr(args, "listen_model", None) or LISTEN_MODEL).strip()
    budget = getattr(args, "listen_budget_usd", None)
    budget = LISTEN_BUDGET_USD if budget is None else float(budget)
    where = _where(project)
    problems: list[str] = []
    if not gateway.active():
        problems.append(f"the gateway is off: the listener is reached only through "
                        f"Vercel's AI Gateway, which {gateway.SWITCH}=vercel turns on; "
                        "nothing else listens in its place")
    problems += gateway_engine.readiness()
    spec = models.LISTEN_MODELS.get(model)
    if spec is None:
        problems.append(f"{model} is not a model allowed to listen (models.LISTEN_MODELS: "
                        f"{', '.join(models.LISTEN_MODELS)}); pass --listen-model with one")
    elif not tuple(spec.get("providers") or ()):
        problems.append(f"{model} has no provider verified to take audio and answer to a "
                        "schema (models.LISTEN_MODELS); it is refused until a live check "
                        "names one")
    for stage, author in authors(script, sound, voice):
        said = family(stage, author, model)
        if said:
            problems.append(said)
    video = Path(home) / "video.mp4"
    scene_path = Path(scene)
    if not video.is_file():
        problems.append(f"there is no {video} to hear: run {qa._yt('render', where)} first")
    elif scene_path.is_file() and video.stat().st_mtime < scene_path.stat().st_mtime:
        problems.append(f"{video} is older than {scene_path.name}: the film is not this "
                        f"cut; run {qa._yt('render', where)} again")
    if qa.silent(build):
        problems.append("build.json has no audio track: there is nothing to hear")
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    for name, found in tools.items():
        if not found:
            problems.append(f"{name} is not on PATH: --listen takes the film's audio out "
                            "with ffmpeg and measures it with ffprobe")
    scene_sha = qa.scene_digest(scene_path.read_bytes()) if scene_path.is_file() else ""
    if getattr(args, "reuse", None):
        kept = read_json(Path(home) / qa.QA_DIR / args.reuse / "scorecard.json", warn=False)
        drawn = kept.get("scene_sha256") if isinstance(kept, dict) else None
        if drawn != scene_sha:
            problems.append(
                f"--reuse {args.reuse}: its frames were drawn from "
                + (f"another scene ({str(drawn)[:12]})" if drawn else
                   "a scene its scorecard does not record")
                + f" than {scene_path.name} now ({scene_sha[:12]}), while the film heard is "
                "this one; score a new pass instead")
    if problems:
        raise Refused("\n".join(problems))

    # -- the track ------------------------------------------------------------
    film_sha = _sha256(video)
    out_dir = Path(home) / qa.QA_DIR / LISTEN_DIR
    track = out_dir / f"{film_sha[:16]}{SUFFIX}"
    if not (track.is_file() and track.stat().st_size > 0):
        out_dir.mkdir(parents=True, exist_ok=True)
        part = out_dir / f"{film_sha[:16]}.part"
        argv = [tools["ffmpeg"], "-nostdin", "-y", "-v", "error", "-i", str(video),
                "-vn", "-map", "0:a:0", "-c", "copy", "-f", CONTAINER, str(part)]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if done.returncode != 0 or not part.is_file() or part.stat().st_size == 0:
            part.unlink(missing_ok=True)
            raise Refused(f"ffmpeg could not copy the audio stream out of {video} "
                          f"(exit {done.returncode}): {(done.stderr or '').strip()[-400:]}")
        part.replace(track)
    probe = subprocess.run([tools["ffprobe"], "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=duration", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, timeout=120)
    said = next((ln.strip() for ln in (probe.stdout or "").splitlines() if ln.strip()), "")
    try:
        seconds = float(said)
    except ValueError:
        seconds = float("nan")
    if probe.returncode != 0 or not math.isfinite(seconds) or seconds <= 0:
        raise Refused(f"ffprobe gave no duration for the audio of {video} ({said or 'nothing'}), "
                      "so no finding's time could be checked against it")
    size = track.stat().st_size
    if size > LISTEN_MAX_MB * 1024 * 1024:
        raise Refused(f"the film's audio is {size / 1024 / 1024:.2f} MB, over "
                      f"LISTEN_MAX_MB={LISTEN_MAX_MB:g} MB, the most one request carries")
    audio = {"path": str(track), "sha256": _sha256(track), "bytes": size,
             "seconds": round(seconds, 3), "media_type": gateway_engine.media_type(track)}
    film = {"path": str(video), "sha256": film_sha, "mtime": video.stat().st_mtime}

    # -- the question, and what it may cost ---------------------------------
    prompt = listen_prompt(title, seconds, script=script, build=build, voice=voice,
                           sound=sound)
    system = "\n\n".join(p for p in (LISTEN_ROLE, house) if p)
    # The transcript (AUTO-152): the shots that carry words, unless the film
    # is wordless; neither asks anything.
    shots: list[dict] = []
    if isinstance(voice, dict) and voice.get("wordless"):
        skip = "a wordless film: voice.json has no takes, so there are no words to hear"
    else:
        shots = voiced_shots(build, script)
        skip = "" if shots else "no shot in build.json is voiced: there are no words to hear"
    estimate = estimate_usd(model, seconds, len(prompt) + len(system) + 2000,
                            transcript=bool(shots),
                            transcript_chars=len(transcript_prompt(shots, seconds))
                            + len(system) + 2000)
    if estimate is None:
        raise Refused(f"{model} has no price (GATEWAY_PRICES or the price table), so its "
                      "cost cannot be held to --listen-budget-usd; set its row")
    if estimate > budget:
        raise Refused(f"the listen is estimated at ${estimate:.4f} for {seconds:g} s of audio, "
                      f"over --listen-budget-usd {budget:g}")
    return Prepared(model=model, providers=list(spec.get("providers") or ()), film=film,
                    audio=audio, scene_sha256=scene_sha, scene_path=str(scene_path),
                    prompt=prompt, system=system,
                    estimate_usd=estimate, budget_usd=budget,
                    shots=shots, transcript_skip=skip)


def listen_prompt(title: str, seconds: float, *, script: dict, build: dict, voice: dict,
                  sound: dict) -> str:
    """What the listener is asked. Nothing in it names the pass, so the same
    film asks the same question and is served from decisions/qa/."""
    from . import qa
    said = qa.narration_text(script, build, voice)
    # A cue's time is where the build placed it on its word (cue_anchors),
    # else the sheet's `at`; a build.json without anchors asks as before.
    placed = qa.placed_cue_times(build)
    cues = [{k: (placed.get(str(c.get("cue_id")), c.get(k)) if k == "at" else c.get(k))
             for k in ("at", "seconds", "kind", "level", "motif")
             if c.get(k) is not None} for c in (sound or {}).get("cues") or []
            if isinstance(c, dict)][:80]
    return "\n\n".join([
        f"Listen to the attached audio: the whole soundtrack of \"{title}\", "
        f"{seconds:g} s, copied out of the rendered film with nothing re-encoded, the mix "
        "as its viewer hears it. For each of voiceover, sound_design and music_score say "
        "in `heard` what you heard, then file every problem a viewer would notice as a "
        f"finding: its `timestamp` in seconds from the start of this audio (a number from "
        f"0 to {seconds:g}), what is audible, why it lowers quality, the root cause, the "
        "owner (exactly one of those below) and the fix; `blocking` when a viewer would "
        "stop watching or stop trusting the film, else `minor`. Say in `heard` how long "
        "the audio was and whether it carries narration, music and effects. What the audio "
        "alone cannot settle goes under not_heard.",
        brief("The narration as written: what the film should say, context and not "
              "evidence of what is audible", said.splitlines() if said else []),
        brief("The cue sheet as spotted: context, not evidence", cues),
        *picture_timeline(build),
        brief("Each finding's owner is exactly one of these; one you cannot place is "
              "`external`, with the reason in `fix`",
              {o: qa.DUTIES[o] for o in LISTEN_OWNERS}),
    ])


#: The shot roles whose start is a reveal, as build.beat_sync reads them.
_REVEALS = ("reveal", "payoff")


def picture_timeline(build: dict | None) -> list[str]:
    """The picture's timeline as `yt build` laid it (AUTO-140), for the
    listener to judge timing against by ear: every cut, each reveal with its
    line, each anchored cue at its placed time with its word, and how the
    cuts sit on the music's beats. One brief, or none when build.json has
    neither cue_anchors nor beat_sync, so an older build asks as before."""
    build = build or {}
    if not ("cue_anchors" in build or "beat_sync" in build):
        return []
    shots = sorted((s for s in build.get("shots") or [] if isinstance(s, dict)),
                   key=lambda s: float(s.get("at") or 0))
    cues = []
    for row in build.get("cue_anchors") or []:
        anchor = row.get("anchor") or {}
        cues.append({"cue": row.get("cue"), "at": row.get("placed_at"),
                     "shot": anchor.get("shot"),
                     "word": anchor.get("text") if int(anchor.get("word", -1)) >= 0
                     else "shot start",
                     "status": row.get("status")})
    sync = build.get("beat_sync")
    return [brief("The picture's timeline as built: context, not evidence", {
        "cuts": [s.get("at") for s in shots[1:]],
        "reveals": [{"label": s.get("label"), "at": s.get("at"),
                     "line": str(s.get("narration") or "")[:200]}
                    for s in shots if s.get("function") in _REVEALS],
        "cues": cues,
        **({"beats": {k: v for k, v in sync.items() if k != "cuts"}}
           if isinstance(sync, dict) else {}),
    })]


# --------------------------------------------------------------------------- #
# the listen
# --------------------------------------------------------------------------- #

@dataclass
class _Listener(Harness):
    """The listener's harness, keeping each engine it built so the record
    can say which model and provider served the answer."""
    built: list = field(default_factory=list, repr=False, compare=False)

    def _engine(self, **config: Any):
        engine = super()._engine(**config)
        self.built.append(engine)
        return engine

    def served(self) -> tuple[str | None, str | None]:
        """The model that answered, and the provider that served it, from
        the last call made. TO VERIFY live: the routing field's name."""
        for engine in reversed(self.built):
            for payload in reversed(engine.log):
                routing = payload.get("routing") if isinstance(payload.get("routing"), dict) else {}
                provider = next((routing.get(k) for k in ("resolvedProvider", "finalProvider",
                                                          "provider") if routing.get(k)), None)
                return payload.get("model"), provider
        return None, None


def served_before(pass_dir: Path, decisions: list[dict]) -> tuple[str | None, str | None]:
    """Who served an answer this run took from decisions/qa/: the latest
    earlier listening.json that holds the same decisions (name and digest,
    so the same question) and says who answered. The cache keeps the
    answer, not the routing, so the record of the pass that asked does.
    ("cached", "cached") when no such record is left."""
    asked = [(d.get("name"), d.get("digest")) for d in decisions if isinstance(d, dict)]
    for path in sorted(Path(pass_dir).parent.glob(f"pass-*/{LISTENING}"), reverse=True):
        if path.parent == Path(pass_dir):
            continue
        old = read_json(path, warn=False)
        if not isinstance(old, dict) or old.get("served_model") in (None, "cached"):
            continue
        if [(d.get("name"), d.get("digest")) for d in old.get("decisions") or []
                if isinstance(d, dict) and d.get("kind") != "failed"] == asked:
            return old.get("served_model"), old.get("provider")
    return "cached", "cached"


def _problem(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def _same(a: dict, b: dict) -> bool:
    """Two kept findings that say one thing: one category, and the same
    problem, or the same owner within half a second."""
    if a.get("category") != b.get("category"):
        return False
    if _problem(a.get("problem")) == _problem(b.get("problem")):
        return True
    return (a.get("owner") == b.get("owner")
            and abs(float(a["timestamp"]) - float(b["timestamp"])) <= 0.5)


def merged(first: tuple[list[dict], list[dict]], second: tuple[list[dict], list[dict]]
           ) -> tuple[list[dict], list[dict]]:
    """The first answer's kept findings with what the ask-again adds: each
    finding it kept that the first did not say (its replacements for the
    findings that failed), and dropped what failed in the first and was
    not replaced by one of the same problem, plus what failed again."""
    kept1, dropped1 = first
    kept2, dropped2 = second
    added = [f for f in kept2 if not any(_same(f, k) for k in kept1)]

    def key(category: Any, finding: Any) -> tuple:
        return category, _problem((finding or {}).get("problem"))
    replaced = {key(f.get("category"), f) for f in added}
    failed = {key(d.get("category"), d.get("finding")) for d in dropped1}
    dropped = [d for d in dropped1 if key(d.get("category"), d.get("finding")) not in replaced]
    dropped += [d for d in dropped2 if key(d.get("category"), d.get("finding")) not in failed]
    return kept1 + added, dropped


def check(result: Any, seconds: float) -> tuple[list[dict], list[dict], list[str]]:
    """The answer past the schema: (kept findings, dropped [{finding,
    category, why}], the problems as one line each, for an ask-again)."""
    kept, dropped, problems = [], [], []
    cats = (result or {}).get("categories") if isinstance(result, dict) else None
    for key in LISTEN_CRITERIA:
        cat = (cats or {}).get(key) if isinstance(cats, dict) else None
        rows = (cat or {}).get("findings") if isinstance(cat, dict) else None
        for i, row in enumerate(rows if isinstance(rows, list) else []):
            why = []
            if not isinstance(row, dict):
                why.append("it is not a finding")
                row = {"problem": str(row)}
            else:
                t = row.get("timestamp")
                if isinstance(t, bool) or not isinstance(t, (int, float)) \
                        or not math.isfinite(float(t)):
                    why.append(f"its timestamp {t!r} is not a number of seconds")
                elif not 0 <= float(t) <= seconds:
                    why.append(f"its timestamp {float(t):g} is outside the audio "
                               f"(0 to {seconds:g} s)")
                if row.get("owner") not in LISTEN_OWNERS:
                    why.append(f"its owner {row.get('owner')!r} is not one of "
                               f"{', '.join(LISTEN_OWNERS)}")
                if row.get("severity") not in SEVERITIES:
                    why.append(f"its severity {row.get('severity')!r} is not one of "
                               f"{', '.join(SEVERITIES)}")
                if not str(row.get("problem") or "").strip():
                    why.append("it names no problem")
            if why:
                text = "; ".join(why)
                dropped.append({"finding": row, "category": key, "why": text})
                problems.append(f"categories.{key}.findings[{i}] "
                                f"({str(row.get('problem') or '')[:80]!r}): {text}")
            else:
                kept.append(dict(row, timestamp=float(row["timestamp"]), category=key,
                                 source="listen", frame=LISTENING))
    return kept, dropped, problems


# --------------------------------------------------------------------------- #
# the transcript: did the narration say the placed words? (AUTO-152)
# --------------------------------------------------------------------------- #

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"))
_ORDINALS = {"one": "first", "two": "second", "three": "third", "five": "fifth",
             "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
#: A number as written: thousands grouped by commas or not, a decimal part,
#: an ordinal ending, a percent sign.
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(st|nd|rd|th)?(?!\w)"
                     r"(\s*%)?")
#: A word: letters and digits, joined inside by apostrophes and hyphens.
_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")


def _hundreds(n: int) -> list[str]:
    out: list[str] = []
    if n >= 100:
        out += [_ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n or not out:
        out.append(_ONES[n])
    return out


def number_words(n: int) -> list[str]:
    """An integer as it is said ("twenty-one", "three hundred five", no
    "and"), up to a billion; a longer one digit by digit."""
    if n > 10 ** 9:
        return [_ONES[int(d)] for d in str(n)]
    if n < 1000:
        return _hundreds(n)
    out: list[str] = []
    for scale, name in _SCALES:
        if n >= scale:
            out += _hundreds(n // scale) + [name]
            n %= scale
    return out + (_hundreds(n) if n else [])


def _ordinal(word: str) -> str:
    head, sep, last = word.rpartition("-")
    last = (_ORDINALS.get(last) or (last[:-1] + "ieth" if last.endswith("y") else last + "th"))
    return head + sep + last


def _spell(m: re.Match) -> str:
    whole, decimals, ordinal, percent = m.groups()
    words = number_words(int(whole.replace(",", "")))
    if ordinal and not decimals:
        words[-1] = _ordinal(words[-1])
    if decimals:
        words += ["point"] + [_ONES[int(d)] for d in decimals]
    if percent:
        words.append("percent")
    return f" {' '.join(words)} "


def normalise_words(text: Any) -> list[str]:
    """The words of `text` for comparing what was said with what was
    written: lower case, punctuation gone but an apostrophe or hyphen inside
    a word kept, and numbers spelled out (1.5% is "one point five percent")."""
    text = str(text or "").replace("’", "'").replace("‘", "'")
    text = _NUMBER.sub(_spell, text).replace("%", " percent ")
    return _WORD.findall(text.lower())


def wer(expected: list[str], heard: list[str]) -> float:
    """The word error rate of `heard` against `expected`: the words replaced
    (the longer side of each replaced run), deleted and inserted, over the
    words expected. 0.0 when both are empty, 1.0 for words where none were
    expected."""
    if not expected:
        return 0.0 if not heard else 1.0
    errors = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, expected, heard,
                                                      autojunk=False).get_opcodes():
        if op == "replace":
            errors += max(i2 - i1, j2 - j1)
        elif op == "delete":
            errors += i2 - i1
        elif op == "insert":
            errors += j2 - j1
    return errors / len(expected)


def _drop(words: list[str], seq: list[str]) -> tuple[list[str], int]:
    """`words` without each run of `seq`, and how many there were."""
    if not seq:
        return words, 0
    out, n, i = [], 0, 0
    while i < len(words):
        if words[i:i + len(seq)] == seq:
            i += len(seq)
            n += 1
        else:
            out.append(words[i])
            i += 1
    return out, n


def compare_words(expected: str, heard: str, *, pronounce=()) -> dict:
    """What was written against what was heard: the WER, with each pronouncer
    term left out of both sides (written as the term, heard as the term or
    its `say`, which a transcriber spells as it likes), and how many of the
    written terms were left out."""
    want, got = normalise_words(expected), normalise_words(heard)
    dropped = 0
    for row in pronounce or ():
        if not isinstance(row, dict):
            continue
        want, n = _drop(want, normalise_words(row.get("term")))
        dropped += n
        for said in (row.get("term"), row.get("say")):
            got, _ = _drop(got, normalise_words(said))
    return {"wer": round(wer(want, got), 4), "pronounced": dropped}


def voiced_shots(build: dict | None, script: dict | None = None) -> list[dict]:
    """The shots build.json placed with words to say, in film order: label,
    `at`, `seconds`, the narration (what the transcript is held to) and the
    screenplay's pronouncers for the shot."""
    from .script import spoken
    terms: dict[str, list] = {}
    sections = script.get("sections") if isinstance(script, dict) else None
    for sec in sections if isinstance(sections, list) else []:
        planned = sec.get("shots") if isinstance(sec, dict) else None
        for shot in planned if isinstance(planned, list) else []:
            if isinstance(shot, dict) and isinstance(shot.get("pronounce"), list):
                terms[str(shot.get("label"))] = [r for r in shot["pronounce"]
                                                 if isinstance(r, dict)]
    out = []
    built = build.get("shots") if isinstance(build, dict) else None
    for shot in built if isinstance(built, list) else []:
        if not isinstance(shot, dict):
            continue
        # the take text as placed (the caption's), whitespace collapsed;
        # spoken() only says whether it is words or a bracketed direction
        words = spoken(shot.get("narration") or "")
        try:
            at, seconds = float(shot.get("at")), float(shot.get("seconds"))
        except (TypeError, ValueError):
            continue
        if words and math.isfinite(at) and math.isfinite(seconds):
            label = str(shot.get("label"))
            out.append({"label": label, "at": at, "seconds": seconds, "narration": words,
                        "pronounce": terms.get(label, [])})
    return sorted(out, key=lambda s: s["at"])


def transcript_prompt(shots: list[dict], seconds: float) -> str:
    """What the transcriber is asked: each shot's label and place on the
    film, never the words it should hear, so it writes what it hears."""
    return "\n\n".join([
        f"Transcribe the attached audio, {seconds:g} s long, shot by shot. For each shot "
        "listed below, write in `words` exactly what the narrator says during it, as you "
        "hear it: no correcting, no tidying, a word you cannot make out written as it "
        "sounded. Give `start` and `end`, in seconds from the beginning of the audio, for "
        "where its speech begins and ends. Answer for every shot listed, by its `label`, "
        "and for no other.",
        brief("The shots, each at its place on the film (`at` and `seconds`, in seconds)",
              [{"label": s["label"], "at": s["at"], "seconds": s["seconds"]} for s in shots]),
    ])


def _number(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def transcript_check(result: Any, shots: list[dict], seconds: float
                     ) -> tuple[dict[str, dict], dict[str, str]]:
    """The transcript past the schema: ({label: {start, end, words}} kept,
    {label: why} for each planned shot that is missing, has no words, ends
    before it starts, runs past the track, or starts outside its shot (each
    give or take TRANSCRIPT_TIME_SLACK_S)."""
    slack = TRANSCRIPT_TIME_SLACK_S
    rows = result.get("shots") if isinstance(result, dict) else None
    answered: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            answered.setdefault(str(row.get("label")), row)
    kept: dict[str, dict] = {}
    problems: dict[str, str] = {}
    for shot in shots:
        label = shot["label"]
        row = answered.get(label)
        if row is None:
            problems[label] = f"shot {label} is missing from the answer"
            continue
        start, end = row.get("start"), row.get("end")
        if not (_number(start) and _number(end)):
            problems[label] = (f"shot {label}: its start {start!r} and end {end!r} are not "
                               "both numbers of seconds")
            continue
        start, end = float(start), float(end)
        at, length = float(shot["at"]), float(shot["seconds"])
        if not isinstance(row.get("words"), str):
            problems[label] = f"shot {label}: its words are not text"
        elif start < 0 or end > seconds + slack:
            problems[label] = (f"shot {label}: {start:g} to {end:g} s runs outside the track "
                               f"(0 to {seconds:g} s)")
        elif end <= start:
            problems[label] = (f"shot {label}: it ends at {end:g} s, before or at its start "
                               f"{start:g} s")
        elif not at - slack <= start <= at + length + slack:
            problems[label] = (f"shot {label}: its speech starts at {start:g} s, outside its "
                               f"shot ({at:g} to {at + length:g} s, give or take {slack:g} s)")
        else:
            kept[label] = {"start": start, "end": end, "words": row["words"]}
    return kept, problems


def _clip(text: Any, n: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def transcript_record(reason: str, model: str, shots: list[dict], *, why: str = "") -> dict:
    """A transcript that compared nothing, not evidenced for `reason`: every
    shot in `shots` named unheard for `why` (none when it was skipped, with
    no call, for a wordless film or one with no voiced shot)."""
    return {"status": "not_evidenced", "reason": reason, "model": model, "shots": [],
            "flagged": [],
            "not_evidenced": [{"label": s["label"], "why": why or reason} for s in shots],
            "findings": []}


def transcribe(listener: Harness, prepared: Prepared, shots: list[dict], *,
               where: str = "") -> dict:
    """The transcript, on the listener's harness with the same track: each
    voiced shot's words as heard and where, held to its narration as built.
    A shot whose answer fails the check is asked once more, alone with the
    others that failed, then named under not_evidenced; one heard over
    INTELLIGIBILITY_WER is a voiceover finding owned by voice at the shot's
    time. A call that fails leaves every shot not evidenced."""
    from . import qa
    seconds = float(prepared.audio["seconds"])
    name = f"transcript-{prepared.audio['sha256'][:16]}"
    media = [prepared.audio["path"]]
    try:
        result = listener.decide(name, transcript_prompt(shots, seconds), TRANSCRIPT_SCHEMA,
                                 media=media)
    except HarnessError as exc:
        return transcript_record(str(exc), prepared.model, shots,
                                 why="the transcript did not answer")
    kept, problems = transcript_check(result, shots, seconds)
    if problems:
        failing = [s for s in shots if s["label"] in problems]
        again = (transcript_prompt(failing, seconds) + "\n\nYour last answer for these "
                 "shots could not be kept: " + "; ".join(problems[s["label"]] for s in failing)
                 + ". Answer again for these shots only, each by its label, its `start` "
                 "inside its shot and its `end` inside the audio.")
        try:
            second = listener.decide(f"{name}-again", again, TRANSCRIPT_SCHEMA, media=media)
        except HarnessError as exc:
            listener._say(f"[qa] {name}-again: {exc}; the shots it asked after are not "
                          "evidenced")
        else:
            more, problems = transcript_check(second, failing, seconds)
            kept.update(more)
    rows, flagged, findings = [], [], []
    for shot in shots:
        label = shot["label"]
        if label not in kept:
            continue
        heard = kept[label]["words"]
        said = compare_words(shot["narration"], heard, pronounce=shot.get("pronounce"))
        rows.append({"label": label, "at": shot["at"], "expected": shot["narration"],
                     "heard": heard, "wer": said["wer"], "pronounced": said["pronounced"]})
        if said["wer"] > INTELLIGIBILITY_WER:
            flagged.append(label)
            findings.append({
                "timestamp": float(shot["at"]),
                "problem": (f"shot {label}: heard '{_clip(heard)}' for "
                            f"'{_clip(shot['narration'])}' (WER {said['wer']:.2f})"),
                "why": (f"the narration is not heard as written: a word error rate of "
                        f"{said['wer']:.2f}, over INTELLIGIBILITY_WER "
                        f"{INTELLIGIBILITY_WER:g} (provisional)"),
                "root_cause": f"the take of shot {label} is unclear here, or the mix covers it",
                "owner": "voice",
                "fix": (f"hear it, then retake it ({qa._yt(f'voice --redo {label}', where)}), "
                        "or lower what covers it in the mix"),
                "severity": "minor", "category": "voiceover", "source": "listen",
                "frame": LISTENING})
    unheard = [{"label": s["label"], "why": problems[s["label"]]} for s in shots
               if s["label"] not in kept]
    return {"status": "done" if rows else "not_evidenced",
            "reason": "" if rows else "no shot's transcript could be kept",
            "model": prepared.model, "shots": rows, "flagged": flagged,
            "not_evidenced": unheard, "findings": findings}


def listen(project, pass_dir: Path, prepared: Prepared, args, *, house: str = "",
           house_digest: dict | None = None) -> dict:
    """Ask, check, record: the listen's record, also written to
    `pass_dir/listening.json`. A listen that fails is recorded not current,
    with its reason, its cost and an `external` finding that says so; it
    never stops the frame review."""
    from . import qa
    listener = _Listener(project.root, "qa", model=prepared.model, role=LISTEN_ROLE,
                         house=house, house_digest=house_digest or {},
                         fresh=getattr(args, "fresh", False), verbose=not args.quiet,
                         budget_usd=prepared.budget_usd, feedback=False, timeout=900.0,
                         engine="gateway",
                         gateway={"providers": list(prepared.providers),
                                  "max_media_mb": LISTEN_MAX_MB})
    seconds = float(prepared.audio["seconds"])
    name = f"listen-{prepared.audio['sha256'][:16]}"
    media = [prepared.audio["path"]]
    reason = ""
    result: Any = None
    second: Any = None
    kept: list[dict] = []
    dropped: list[dict] = []
    try:
        result = listener.decide(name, prepared.prompt, LISTEN_SCHEMA, media=media)
        kept, dropped, problems = check(result, seconds)
        if problems:
            # Only the findings that failed are asked again: the ones the
            # check kept stand, so a second answer that leaves them out
            # loses nothing (and one that repeats them adds nothing).
            again = (prepared.prompt + "\n\nYour last answer filed findings this check "
                     "cannot keep: " + "; ".join(problems[:10]) + ". Answer again, filing "
                     "under findings only these, corrected: every timestamp inside the "
                     "audio, every owner and severity one of the schema's values. The "
                     "findings the check kept are kept, so do not file them again. A "
                     "finding you cannot place, leave out.")
            try:
                second = listener.decide(f"{name}-again", again, LISTEN_SCHEMA, media=media)
            except HarnessError as exc:
                listener._say(f"[qa] {name}-again: {exc}; the first answer's sound "
                              "findings are kept")
            else:
                more, failed, _ = check(second, seconds)
                kept, dropped = merged((kept, dropped), (more, failed))
    except HarnessError as exc:
        reason = str(exc)
    # The transcript (AUTO-152): what the narration said, shot by shot, on
    # the same harness and track; asked only after the listen answered.
    shots = list(prepared.shots or [])
    if not shots:
        transcript = transcript_record(prepared.transcript_skip, prepared.model, [])
    elif reason:
        transcript = transcript_record(f"the listen did not answer: {reason}",
                                       prepared.model, shots)
    else:
        transcript = transcribe(listener, prepared, shots, where=_where(project))
    if not reason:
        # Current only while the film heard is the film there: a render
        # that landed during the call carries another mix.
        video, scene = Path(prepared.film["path"]), Path(prepared.scene_path)
        if not video.is_file() or _sha256(video) != prepared.film["sha256"]:
            reason = f"{video.name} changed while the listener heard it; listen again"
        elif scene.is_file() and video.stat().st_mtime < scene.stat().st_mtime:
            reason = f"{video.name} is now older than {scene.name}; render and listen again"
        kept, dropped = ([], []) if reason else (kept, dropped)
        if reason and shots:
            transcript = transcript_record(reason, prepared.model, shots)
    kept = kept + transcript.pop("findings")
    served, provider = listener.served()
    report = listener.report()
    if served is None and not listener.built and report["decisions"]:
        # Nothing was asked: every answer came from decisions/qa/.
        served, provider = served_before(pass_dir, report["decisions"])
    current = not reason
    findings = kept
    if reason:
        findings = [{"timestamp": 0.0,
                     "problem": "the listening pass is not evidence of this film's audio",
                     "why": f"nothing heard the film's rendered audio in {pass_dir.name}, so "
                            f"{', '.join(LISTEN_CRITERIA)} rest on the other reviews alone",
                     "root_cause": reason, "owner": "external",
                     "fix": f"settle what stopped it, then run "
                            f"{qa._yt('qa --listen', _where(project))} again",
                     "severity": "minor", "source": "listen", "frame": LISTENING}]
    record = {
        "pass": pass_dir.name, "model": prepared.model, "served_model": served,
        "provider": provider, "providers": list(prepared.providers),
        "audio": dict(prepared.audio), "film": dict(prepared.film),
        "scene_sha256": prepared.scene_sha256, "current": current, "reason": reason,
        "estimate_usd": prepared.estimate_usd, "budget_usd": prepared.budget_usd,
        "decisions": report["decisions"], "result": result, "again": second,
        "findings": findings, "dropped": dropped,
        "not_heard": [str(x) for x in ((result or {}).get("not_heard") or [])
                      if str(x).strip()] if isinstance(result, dict) and current else [],
        "transcript": transcript,
        "engine": report,
    }
    write_json(pass_dir / LISTENING, record)
    return record


# --------------------------------------------------------------------------- #
# what the scorecard takes from it
# --------------------------------------------------------------------------- #

def evidence_row(record: dict, where: str = "") -> dict:
    """The listen as a listening-evidence row (AUTO-105's shape): it covers
    every AUDIO category, and is current when it answered about the film
    this pass reviews."""
    from . import qa
    sha = (record.get("audio") or {}).get("sha256")
    row = {"source": "listen", "file": f"{qa.QA_DIR}/{record.get('pass')}/{LISTENING}",
           "review": f"{record.get('model')} heard the rendered film's own audio "
                     "(`yt qa --listen`): the whole mix as the viewer hears it",
           "covers": list(qa.AUDIO), "heard": sha, "now": sha,
           "current": bool(record.get("current")), "why": "",
           "command": qa._yt("qa --listen", where),
           # the one review that hears the rendered track (AUTO-152)
           "basis": "heard"}
    if row["current"]:
        row["said"] = {"heard": (record.get("result") or {}).get("heard"),
                       "categories": {k: ((record.get("result") or {}).get("categories")
                                          or {}).get(k, {}).get("heard")
                                      for k in LISTEN_CRITERIA},
                       "findings": [{k: f.get(k) for k in ("timestamp", "category",
                                                            "severity", "problem")}
                                    for f in record.get("findings") or []][:40],
                       "not_heard": record.get("not_heard") or []}
    else:
        row["why"] = f"the listen did not answer: {record.get('reason')}"
    return row


def brief_for(record: dict) -> str:
    """What the scorecard's reviewer is told of a current listen."""
    secs = (record.get("audio") or {}).get("seconds")
    return (f"Listening review: {record.get('model')} heard the rendered audio of this cut "
            f"({secs:g} s, the film's own track) and filed {len(record.get('findings') or [])} "
            f"finding(s), in `{LISTENING}` here and in the listening evidence above. Score "
            "voiceover, sound_design and music_score from what it heard; do not repeat its "
            "findings.")


def merge_into(card: dict, record: dict) -> dict:
    """The scorecard with the listen's findings among its own, and what it
    dropped or could not hear named under not_evidenced."""
    card = dict(card)
    card["findings"] = list(card.get("findings") or []) + list(record.get("findings") or [])
    noted = list(card.get("not_evidenced") or [])
    for row in record.get("dropped") or []:
        finding = row.get("finding") or {}
        noted.append(f"listen: dropped \"{finding.get('problem', '')}\" "
                     f"({row.get('category')}): {row.get('why')}")
    for item in record.get("not_heard") or []:
        noted.append(f"listen: not heard -- {item}")
    words = record.get("transcript") if isinstance(record.get("transcript"), dict) else {}
    unheard = words.get("not_evidenced") or []
    if words.get("status") == "not_evidenced" and not words.get("shots"):
        noted.append("listen: transcript not evidenced"
                     + (f" for {len(unheard)} voiced shot(s)" if unheard else "")
                     + f" -- {words.get('reason')}")
    else:
        for row in unheard:
            noted.append(f"listen: transcript of shot {row.get('label')} not evidenced -- "
                         f"{row.get('why')}")
    card["not_evidenced"] = noted
    return card


def card_entry(record: dict) -> dict:
    """The scorecard's `listen`: whether it is current, who heard, what it
    cost, and the transcript's tally."""
    entry = {k: record.get(k) for k in ("current", "model", "served_model", "provider",
                                        "reason", "decisions", "engine")} | {
        "audio_sha256": (record.get("audio") or {}).get("sha256"),
        "film_sha256": (record.get("film") or {}).get("sha256"),
        "file": LISTENING}
    words = record.get("transcript")
    if isinstance(words, dict):
        entry["transcript"] = {"status": words.get("status"), "reason": words.get("reason"),
                               "shots": len(words.get("shots") or []),
                               "flagged": len(words.get("flagged") or []),
                               "not_evidenced": len(words.get("not_evidenced") or [])}
    return entry
