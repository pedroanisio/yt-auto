"""yt qa --watch: a model that watches the rendered cut whole (AUTO-127).

QA judges stills. A still sampler misses what happens between its samples:
a two-frame pop, a transition that stutters, a super that lands late, motion
that stalls between two stills. No Anthropic or OpenAI model takes video, so
`--watch` is the opt-in pass that sends the cut, through Vercel's AI Gateway,
to a model of another maker that watches it, and turns what it saw into
moments QA renders and its own reviewer confirms:

1. Preflight, before a pass directory is made and before anything is
   rendered, uploaded or paid for: the gateway is on (YT_GATEWAY=vercel)
   and its engine ready, the catalog (GET /v1/models) is reached, the model
   passes the gate (`model_error`: a language model tagged video-input and
   structured-output, not deprecated, not `vmc/`, not `-batch`, from another
   maker than every model that made the film), it has providers verified to
   take video (WATCH_PROVIDERS), and a render host is there to cut the
   master down. Each failure is named and qa exits 1; nothing is replaced
   by something else. `--reuse PASS --watch` needs only that pass's
   watch.json.
2. The cut is the project's video.mp4. When doctor's rules say it is stale
   or partial (`doctor.cut_problems`), nothing is watched or uploaded: the
   problem and its `yt render` command go under not_evidenced and scoring
   goes on. An earlier pass's watch.json of the same cut, model, constants
   and role is copied (`reused_from`) unless --fresh.
3. The plan (`watch_plan`): the cut as it is when it fits WATCH_MAX_BYTES
   and WATCH_MAX_SECONDS, else re-encoded at WATCH_HEIGHT and WATCH_KBPS
   with no audio, split at shot boundaries when it is still too long or too
   large. The re-encode runs on a render host (`prepare_remote`), never on
   this workstation: the cut goes up once, one ssh cuts every segment, the
   segments come back, the host's directory is removed.
4. One decision per segment through the gateway engine, the segment as
   media (`watch-sNN`), cached by its bytes. The answer is checked past the
   schema (times inside the segment, `watched_seconds` near its length) and
   asked once more (`watch-sNN-again`) with the problems named; a segment
   that still fails, or whose call fails, is recorded unwatched. A budget
   402 stops the watch; the segments left are recorded unwatched.
5. The record, `qa/<pass>/watch.json`: segment times shifted to film times,
   findings in an overlap merged, ids W1.. . Each finding's start becomes a
   still of the pass (qa.sample's `watched`), and the scorecard's reviewer
   says of each whether its still shows it. Only a finding at least two
   scoring samples confirm with its still enters the scorecard's findings
   (`source: watched:Wn`); the rest are listed, blocking nothing.

The constants below are the card's starting values, unmeasured: each is
marked TO VERIFY until a live call on the owner's machine sets it.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import doctor, gateway, models, qa, render
from .core import read_json, write_json
from .harness import Harness, HarnessError, brief

#: The watcher when --watch-model is not given: the model Vercel's
#: video-input page uses, tagged video-input and structured-output in the
#: 2026-10-23 catalog, $0.75 / $3.75 per 1M tokens.
WATCH_MODEL = "google/gemini-3.6-flash"

#: The providers each watcher may be served by (the engine's `only`). The
#: catalog cannot say which provider takes video with a schema, so a model
#: whose list is empty is refused until a live check names its providers.
#: TO VERIFY live (2026-10-23 defaults): google for gemini-3.6-flash;
#: kimi-k3 (17 providers) and glm-5.3-flash (22) are left empty.
WATCH_PROVIDERS: dict[str, tuple[str, ...]] = {
    "google/gemini-3.6-flash": ("google",),
    "moonshotai/kimi-k3": (),
    "zai/glm-5.3-flash": (),
}

#: The price rows the estimate reads, in USD per 1M tokens, pinned from the
#: catalog of 2026-10-23 07:25. The estimate never fetches.
WATCH_PRICE: dict[str, dict[str, float]] = {
    "google/gemini-3.6-flash": {"input": 0.75, "output": 3.75},
    "moonshotai/kimi-k3": {"input": 3.0, "output": 15.0},
    "zai/glm-5.3-flash": {"input": 0.15, "output": 0.50},
}

#: The most one request carries, in bytes. TO VERIFY live: the size probe
#: (5, 15, 19, 21, 50 and 100 MB) through the gateway; 14 MiB is about
#: 19 MB as base64, under Gemini's 20 MB request figure.
WATCH_MAX_BYTES = 14 * 1024 * 1024
#: The longest segment sent, in seconds. TO VERIFY live: the longest each
#: model takes at WATCH_HEIGHT and WATCH_KBPS within its context.
WATCH_MAX_SECONDS = 600.0
#: The height a re-encoded segment is scaled to (open question 4: 480 or
#: 720). TO VERIFY live.
WATCH_HEIGHT = 480
#: The video bitrate a re-encoded segment is held to, in kbit/s.
#: TO VERIFY live.
WATCH_KBPS = 600
#: One watch call's limit, in seconds. TO VERIFY live: the latency of the
#: longest segment accepted.
WATCH_TIMEOUT = 900.0
#: Input tokens per second of video, for the estimate: Gemini's 258 a frame
#: at one frame a second, no audio. TO VERIFY live against a call's usage.
WATCH_TOKENS_PER_SECOND = 258
#: The answer's tokens per segment, for the estimate.
WATCH_OUTPUT_TOKENS = 3000
#: The length an estimate assumes when no build says how long the film is.
ASSUMED_SECONDS = 600
#: How long the host may take to cut every segment.
PREPARE_TIMEOUT = 3600

#: The catalog, read once a run, never without --watch.
CATALOG_URL = "https://ai-gateway.vercel.sh/v1/models"
CATALOG_TIMEOUT = 20.0

#: Where the record and the segments live in the pass directory.
RECORD = "watch.json"
SEGMENTS = "watch"

#: A predicted size's margin over duration x bitrate (the container).
SIZE_MARGIN = 1.02
#: The share of max_bytes a split segment is planned to, so the encoder's
#: overshoot stays under the limit.
SPLIT_SHARE = 0.9
#: The overlap where a segment cuts inside a shot.
OVERLAP = 1.0
#: How far past its segment a time may fall, and how far the host's
#: duration may differ from the plan's.
SLACK = 0.5
#: Two findings in an overlap whose starts are this close are one.
SAME_SECONDS = 1.0

#: What the watcher judges: the categories a moving picture shows. The
#: audio categories are the listener's (AUTO-126), never the watcher's.
CATEGORIES = ("visual_quality", "animation_vfx", "cinematography", "editing_pacing",
              "graphics_overlays", "narrative_clarity")
SEVERITIES = ("blocking", "minor")
VERDICTS = ("confirmed", "not_visible", "disagree")

WATCH_ROLE = (
    "You watch a rendered film as its viewer will, in motion, and report what the "
    "moving picture shows that single stills cannot: a frame or two that pops, a "
    "transition that stutters, a super that lands late or leaves early, motion that "
    "stalls or jumps, a cut that comes before its moment, an overlay that cannot be "
    "read while it is on screen. Judge against the publication bar of "
    "docs/production.md and the house rules given; take the film's look from the "
    "treatment you are given, never a look of your own. Every finding is something "
    "you saw, with when it starts and ends in seconds from the start of the attached "
    "video, what is wrong, why it lowers quality, the stage that owns it and the "
    "fix. Say how many seconds of video you watched. Whatever you could not see "
    "(events between the frames you sampled, the sound) you list under not_evidenced "
    "and never guess.")


class Refused(Exception):
    """--watch cannot run as asked; the message names every reason."""


class WatchError(Exception):
    """The cut could not be prepared to send; nothing was sent."""


def add_flags(p) -> None:
    """--watch and --watch-model on `yt qa`."""
    p.add_argument("--watch", action="store_true",
                   help="also send the rendered cut (video.mp4) to a model that watches it "
                        "(through the gateway: YT_GATEWAY=vercel, its key and Node >=22), cut "
                        "down on a render host when it is too large; each moment it reports "
                        f"becomes a still of the pass, and counts only when the scorecard's "
                        f"reviewer confirms it there ({RECORD})")
    p.add_argument("--watch-model", default=WATCH_MODEL, metavar="ID",
                   help="the watcher: a gateway model tagged video-input and structured-output, "
                        f"of another maker than the film's authors (default {WATCH_MODEL})")


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #

_FINDING = {
    "type": "object",
    "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                   "category": {"type": "string", "enum": list(CATEGORIES)},
                   "problem": {"type": "string"}, "why": {"type": "string"},
                   "owner": {"type": "string", "enum": list(qa.OWNERS)},
                   "fix": {"type": "string"},
                   "severity": {"type": "string", "enum": list(SEVERITIES)}},
    "required": ["start", "end", "category", "problem", "why", "owner", "fix", "severity"],
}
WATCH_SCHEMA = {
    "type": "object",
    "properties": {"watched_seconds": {"type": "number"},
                   "findings": {"type": "array", "items": _FINDING},
                   "not_evidenced": {"type": "array", "items": {"type": "string"}}},
    "required": ["watched_seconds", "findings", "not_evidenced"],
}

#: What the scorecard's reviewer answers of each watched finding.
WATCHED_SCHEMA = {
    "type": "array",
    "items": {"type": "object",
              "properties": {"id": {"type": "string"},
                             "verdict": {"type": "string", "enum": list(VERDICTS)},
                             "frame": {"type": "string"},
                             "why": {"type": "string"}},
              "required": ["id", "verdict", "frame", "why"]},
}


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #

def _get_json(url: str, timeout: float) -> Any:
    """One GET, its body as JSON (the catalog needs no key)."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def catalog(fetch=None) -> list[dict]:
    """The gateway's catalog rows, read once. Refused when it cannot be
    reached: the gate never guesses from a pinned list."""
    try:
        doc = (fetch or _get_json)(CATALOG_URL, CATALOG_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        raise Refused(f"the gateway's catalog ({CATALOG_URL}) could not be read: {exc}; "
                      "the watcher is checked against it and never against a pinned list")
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise Refused(f"the gateway's catalog ({CATALOG_URL}) holds no list of models")
    return [r for r in rows if isinstance(r, dict)]


def catalog_row(model: str, rows: list[dict]) -> dict | None:
    return next((r for r in rows if r.get("id") == model), None)


def model_error(model: str, catalog: list[dict], authors: list[str]) -> str:
    """Why `model` may not watch the film the `authors` made, or "".

    The catalog row must exist, be a language model tagged video-input and
    structured-output, carry no deprecated_at; the id must not be a virtual
    model (`vmc/`) or a batch one (`-batch`). Its maker (models.maker) must
    be known, and differ from the maker of every author, each of which must
    be known too: a maker nobody can place may be the watcher's."""
    model = str(model or "").strip()
    if model.startswith(models.VIRTUAL_PROVIDER + "/"):
        return (f"{model} is a virtual model (vmc/): it may route to any maker, so it cannot "
                "be shown to differ from the film's authors; pass --watch-model with a "
                "provider/model id")
    if model.endswith("-batch"):
        return (f"{model} is a -batch row: priced as batch and unverified on a synchronous "
                "call; pass --watch-model without -batch")
    row = catalog_row(model, catalog)
    if row is None:
        return (f"{model} is not in the gateway's catalog ({CATALOG_URL}); pass --watch-model "
                "with a catalog id")
    tags = [str(t) for t in row.get("tags") or []]
    if row.get("type") != "language":
        return (f"{model} is a {row.get('type')!r} model, not a language model: it makes "
                "video, it does not watch it and answer in words")
    if "video-input" not in tags:
        return (f"{model} is not tagged video-input in the gateway's catalog: it cannot be "
                f"sent video; pass --watch-model with a model that is (default {WATCH_MODEL})")
    if "structured-output" not in tags:
        return (f"{model} is not tagged structured-output in the gateway's catalog: its "
                "answer cannot be held to the watch's schema")
    if row.get("deprecated_at"):
        return f"{model} is deprecated in the gateway's catalog ({row['deprecated_at']})"
    watches = models.maker(model)
    if watches is None:
        return f"nobody can say who made {model}, so it cannot be shown to differ from the authors"
    for author in authors:
        made = models.maker(author)
        if made is None:
            return (f"the film names {author} among its authors, and nobody can say who made it "
                    f"(no maker), so --watch-model {model} cannot be shown to come from "
                    "another maker; rerun that stage with a model whose maker is known")
        if made == watches:
            return (f"the film names {author} among its authors, {made}'s, and --watch-model "
                    f"{model} is {watches}'s too: a maker's models share their training, so one "
                    "does not judge another's work; pass --watch-model with another maker's model")
    return ""


#: The stage records whose models made what is seen, and where each keeps
#: its harness reports.
AUTHOR_FILES = ("script.json", "keyframe.json", "vfx.json", "sound.json")


def _report_models(report: Any) -> list[str]:
    """A harness report's requested model and every model that served it
    (`by_model`); a decision's model is the one asked for, so not read."""
    if isinstance(report, str):
        return [report]
    if not isinstance(report, dict):
        return []
    out = [str(report["model"])] if isinstance(report.get("model"), str) else []
    return out + [str(m) for m in report.get("by_model") or {} if m]


def author_models(root: Path) -> list[tuple[str, str]]:
    """(file, model) for every model the stages that made the film recorded:
    script.json's engine, keyframe.json's elements and review, vfx.json's
    engine and each authored section's, sound.json's engine, and their
    `usage` rows where they keep one."""
    out: list[tuple[str, str]] = []

    def add(name: str, report: Any) -> None:
        for model in _report_models(report):
            model = model.strip()
            if model and (name, model) not in out:
                out.append((name, model))
    for name in AUTHOR_FILES:
        doc = read_json(Path(root) / name, warn=False)
        if not isinstance(doc, dict):
            continue
        engine = doc.get("engine")
        if name == "keyframe.json" and isinstance(engine, dict) and (
                "elements" in engine or "review" in engine):
            add(name, engine.get("elements"))
            add(name, engine.get("review"))
        else:
            add(name, engine)
        add(name, doc.get("usage"))
        if name == "vfx.json":
            for row in doc.get("authored") or []:
                if isinstance(row, dict):
                    add(name, row.get("engine"))
                    add(name, row.get("usage"))
    return out


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Segment:
    """One piece sent: its start and end on the film, and the height it is
    scaled to (None: the cut as it is)."""
    start: float
    end: float
    scale: int | None = None

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


def predicted_bytes(seconds: float, kbps: int) -> float:
    """A re-encode's size: duration x bitrate, plus the container's share."""
    return seconds * kbps * 125 * SIZE_MARGIN


def split_cap(*, max_bytes: int, max_seconds: float, kbps: int) -> float:
    """The longest split segment: under max_seconds and SPLIT_SHARE of max_bytes."""
    return min(float(max_seconds), SPLIT_SHARE * max_bytes / (kbps * 125))


def plan_kind(segments: list[Segment]) -> str:
    if len(segments) == 1 and segments[0].scale is None:
        return "as_is"
    return "downscale" if len(segments) == 1 else "downscale_split"


def _bounds(shots: list[dict], duration: float) -> list[float]:
    out = {0.0, round(float(duration), 3)}
    for shot in shots or []:
        try:
            at = round(float(shot["at"]), 3)
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < at < duration:
            out.add(at)
    return sorted(out)


def watch_plan(duration: float, size: int, shots: list[dict], *, max_bytes: int,
               max_seconds: float, kbps: int, height: int = WATCH_HEIGHT) -> list[Segment]:
    """What is sent (pure). The cut as it is when it fits both limits; else
    one re-encode at `height` when its predicted size and its length fit;
    else re-encoded pieces of at most `split_cap` seconds, cut where a shot
    starts, and a shot longer than the cap cut inside itself with OVERLAP
    seconds shared at that cut only. Splitting always re-encodes: a stream
    copy cuts only at keyframes, and a shot boundary is not one."""
    duration = round(float(duration), 3)
    if size <= max_bytes and duration <= max_seconds:
        return [Segment(0.0, duration, None)]
    if duration <= max_seconds and predicted_bytes(duration, kbps) <= max_bytes:
        return [Segment(0.0, duration, height)]
    cap = split_cap(max_bytes=max_bytes, max_seconds=max_seconds, kbps=kbps)
    bounds = _bounds(shots, duration)
    out: list[Segment] = []
    start = 0.0
    while start < duration - 1e-9:
        fits = [b for b in bounds if start < b <= start + cap + 1e-9]
        if fits:
            end = max(fits)
            out.append(Segment(start, end, height))
            start = end
            continue
        # The shot that starts here is longer than the cap: cut inside it,
        # and start the next piece OVERLAP seconds before that cut.
        end = math.floor((start + cap) * 1000) / 1000      # never past the cap
        out.append(Segment(start, end, height))
        start = round(end - OVERLAP, 3)
    return out


# --------------------------------------------------------------------------- #
# the cut, cut down on a render host
# --------------------------------------------------------------------------- #

@dataclass
class WatchPrep:
    """The transcode as a value, with no ssh in it (gate.RemotePlan's shape):
    the host, its job directory, the rsync up, the one script, the rsync back."""
    host: str
    root: str
    cut: str
    segments: list[Segment]
    up: list[str]
    script: str
    pull: list[str]
    dest: str


def segment_name(n: int) -> str:
    return f"seg-{n:02d}.mp4"


def _host_tools(host: str) -> tuple[str, str]:
    """ffmpeg beside the host's ffprobe, and that ffprobe, as its probe
    found them (sound.measure_remote's lookup): "" when it did not say."""
    known = render._HOSTS.get(host)
    if known is None:
        try:
            known = render.probe(host)
        except Exception:                                    # noqa: BLE001
            return "", ""
    probe = str(getattr(known, "ffprobe", "") or "")
    return (f"{Path(probe).parent}/ffmpeg" if probe else ""), probe


def prepare_script(root: str, segments: list[Segment], *, ffmpeg: str = "",
                   ffprobe: str = "", kbps: int = WATCH_KBPS) -> str:
    """The one script the host runs: ffmpeg on PATH, else beside its
    ffprobe; each segment re-encoded from cut.mp4 with no audio, then its
    bytes and duration printed as `@@OUT n bytes seconds`."""
    q = shlex.quote
    runs = ""
    for n, seg in enumerate(segments, 1):
        name = segment_name(n)
        scale = f"-vf scale=-2:{int(seg.scale)} " if seg.scale else ""
        runs += (f'"$F" -nostdin -hide_banner -loglevel error -y -ss {seg.start:.3f} '
                 f'-t {seg.seconds:.3f} -i cut.mp4 -an {scale}-c:v libx264 -preset veryfast '
                 f'-b:v {kbps}k -maxrate {kbps}k -bufsize {2 * kbps}k -movflags +faststart '
                 f'{name} && echo "@@OUT {n} $(stat -c %s {name}) '
                 f'$("$P" -v error -show_entries format=duration -of csv=p=0 {name})" '
                 f'|| echo "@@FAIL {n}"; ')
    return (f"cd {q(root)} && {{ F=$(command -v ffmpeg 2>/dev/null); [ -n \"$F\" ] || "
            f"F={q(ffmpeg)}; P=$(command -v ffprobe 2>/dev/null); [ -n \"$P\" ] || "
            f"P={q(ffprobe)}; if [ -x \"$F\" ]; then {runs}true; else echo __NO_FFMPEG__; "
            "false; fi; }")


def plan_remote(cut: Path, host: str, slug: str, segments: list[Segment],
                dest: Path) -> WatchPrep:
    """The prep for `segments` of `cut` on `host`, cut into `dest`."""
    root = render.remote_dir(slug, "watch", render.job_tag())
    ffmpeg, ffprobe = _host_tools(host)
    return WatchPrep(
        host=host, root=root, cut=str(cut), segments=list(segments),
        up=render.sync_up_args(Path(cut), host, f"{root}/cut.mp4", file=True, delete=False,
                               excludes=()),
        script=prepare_script(root, segments, ffmpeg=ffmpeg, ffprobe=ffprobe),
        pull=["rsync", "-az", "-e", render.SSH_COMMAND, f"{host}:{root}/seg-*.mp4",
              f"{dest}/"],
        dest=str(dest))


def prepare_remote(prep: WatchPrep, dest: Path | None = None) -> list[dict]:
    """Run the prep: the cut up once (rsync streams it from disk), one ssh,
    the segments back, the host's directory removed whatever happened. Each
    segment as {n, path, bytes, seconds}. WatchError when the host has no
    ffmpeg, a segment failed, is over WATCH_MAX_BYTES, or is more than SLACK
    seconds off its plan: nothing is sent then."""
    dest = Path(dest or prep.dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        render.refuse_remote("rsync")
    except render.RemoteRefused as exc:
        raise WatchError(str(exc)) from exc
    try:
        try:
            up = subprocess.run(prep.up, capture_output=True, text=True)
            if up.returncode != 0:
                raise WatchError(f"the cut could not be sent to {prep.host}: "
                                 f"{(up.stderr or '').strip()[-300:]}")
            render.refuse_remote()
            done = subprocess.run(render.ssh_argv(prep.host, prep.script),
                                  capture_output=True, text=True, timeout=PREPARE_TIMEOUT)
            out = done.stdout or ""
            if "__NO_FFMPEG__" in out:
                raise WatchError(f"no ffmpeg on render host {prep.host} (not on PATH, not "
                                 "beside its ffprobe)")
            said = {int(n): (int(b), float(s)) for n, b, s in re.findall(
                r"^@@OUT (\d+) (\d+) ([0-9.]+)\s*$", out, re.M)}
            failed = [int(n) for n in re.findall(r"^@@FAIL (\d+)", out, re.M)]
            problems = []
            for n, seg in enumerate(prep.segments, 1):
                if n in failed or n not in said:
                    problems.append(f"{segment_name(n)} was not made on {prep.host}")
                    continue
                size, seconds = said[n]
                if size > WATCH_MAX_BYTES:
                    problems.append(f"{segment_name(n)} is {size} bytes, over WATCH_MAX_BYTES "
                                    f"({WATCH_MAX_BYTES})")
                if abs(seconds - seg.seconds) > SLACK:
                    problems.append(f"{segment_name(n)} lasts {seconds:g} s, not the "
                                    f"{seg.seconds:g} s planned")
            if problems:
                raise WatchError("; ".join(problems)
                                 + (f" ({(done.stderr or '').strip()[-200:]})"
                                    if done.returncode and done.stderr else ""))
            render.refuse_remote("rsync")
            back = subprocess.run(prep.pull, capture_output=True, text=True)
            if back.returncode != 0:
                raise WatchError(f"the segments could not be brought back from {prep.host}: "
                                 f"{(back.stderr or '').strip()[-300:]}")
        except (subprocess.TimeoutExpired, OSError, render.RemoteRefused) as exc:
            raise WatchError(f"render host {prep.host}: {exc}") from exc
    finally:
        render.remove_remote(prep.host, prep.root)
    rows = []
    for n, seg in enumerate(prep.segments, 1):
        path = dest / segment_name(n)
        if not path.is_file():
            raise WatchError(f"{segment_name(n)} did not come back from {prep.host}")
        if path.stat().st_size > WATCH_MAX_BYTES:
            raise WatchError(f"{segment_name(n)} is {path.stat().st_size} bytes here, over "
                             f"WATCH_MAX_BYTES ({WATCH_MAX_BYTES})")
        rows.append({"n": n, "path": str(path), "bytes": path.stat().st_size,
                     "seconds": said[n][1]})
    return rows


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

@dataclass
class Prepared:
    """What preflight found: the watch, ready to run, or the reused pass's record."""
    model: str
    providers: list[str] = field(default_factory=list)
    row: dict = field(default_factory=dict)
    authors: list = field(default_factory=list)
    host: str = ""
    reviewer_same_maker: bool = False
    reused: dict | None = None


def _where(project) -> str:
    return "" if project.standalone else f"--project {project.root}"


def preflight(args, project, *, home: Path, fetch=None) -> Prepared:
    """Everything --watch needs, checked before a pass directory is made or
    anything is rendered, uploaded or paid. Refused names what is missing."""
    import gateway_engine
    model = str(getattr(args, "watch_model", None) or WATCH_MODEL).strip()
    reuse = getattr(args, "reuse", None)
    if reuse:
        # A reused pass is never watched again: its record feeds the review.
        kept = read_json(Path(home) / qa.QA_DIR / reuse / RECORD, warn=False)
        if not isinstance(kept, dict):
            raise Refused(f"{reuse} was not watched; run without --reuse to watch")
        return Prepared(model=str(kept.get("model") or model), reused=kept)
    problems: list[str] = []
    if not gateway.active():
        problems.append(f"the gateway is off: the watcher is reached only through Vercel's "
                        f"AI Gateway, which {gateway.SWITCH}=vercel turns on; nothing else "
                        "watches in its place")
    problems += gateway_engine.readiness()
    if problems:
        raise Refused("\n".join(problems))
    rows = catalog(fetch)
    authors = author_models(Path(project.root))
    said = model_error(model, rows, [m for _, m in authors])
    if said:
        problems.append(said)
    providers = tuple(WATCH_PROVIDERS.get(model) or ())
    if not said and not providers:
        problems.append(f"{model} has no provider verified to take video and answer to a "
                        "schema (WATCH_PROVIDERS); it is refused until a live check names one")
    host = getattr(args, "host", None) or render.remote_host(None, "encode")
    if not host:
        problems.append("no render host (RS_RENDER_HOSTS or RS_RENDER_HOST, or --host): "
                        "the cut is cut down there, never on this workstation")
    if problems:
        raise Refused("\n".join(problems))
    row = catalog_row(model, rows) or {}
    makers = {models.maker(m) for _, m in authors}
    return Prepared(model=model, providers=list(providers),
                    row={k: row.get(k) for k in ("id", "type", "tags", "pricing",
                                                  "deprecated_at")},
                    authors=[list(a) for a in authors], host=str(host),
                    reviewer_same_maker=models.maker(getattr(args, "model", None)) in makers)


# --------------------------------------------------------------------------- #
# the watch
# --------------------------------------------------------------------------- #

def constants() -> dict:
    """The plan's constants: a watch made under others is not reused."""
    return {"max_bytes": WATCH_MAX_BYTES, "max_seconds": WATCH_MAX_SECONDS,
            "height": WATCH_HEIGHT, "kbps": WATCH_KBPS}


def role_sha256() -> str:
    return hashlib.sha256(WATCH_ROLE.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def cut_seconds(project, build: dict) -> float:
    """The cut's length: render.json's output range, else the build's."""
    rep = read_json(project.source("render"), warn=False) or {}
    for out in (rep.get("result") or {}).get("outputs") or []:
        rng = out.get("range") if isinstance(out, dict) else None
        if isinstance(rng, list) and len(rng) == 2:
            try:
                return round(float(rng[1]) - float(rng[0]), 3)
            except (TypeError, ValueError):
                pass
    try:
        return round(float(((build or {}).get("scene") or {}).get("duration")), 3)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class _Watcher(Harness):
    """The watcher's harness, keeping each engine it built so the record
    can say which model and provider served each call."""
    built: list = field(default_factory=list, repr=False, compare=False)

    def _engine(self, **config: Any):
        engine = super()._engine(**config)
        self.built.append(engine)
        return engine

    def calls(self) -> list[dict]:
        out = []
        for engine in self.built:
            for payload in engine.log:
                routing = payload.get("routing") if isinstance(payload.get("routing"), dict) else {}
                out.append({"model": payload.get("model"),
                            "generation_id": payload.get("generation_id"),
                            "provider": next((routing.get(k) for k in (
                                "finalProvider", "resolvedProvider", "provider")
                                if routing.get(k)), None),
                            "cost_usd": payload.get("total_cost_usd"),
                            "cost_source": payload.get("cost_source"),
                            "error": bool(payload.get("is_error"))})
        return out


def make_harness(project, prepared: Prepared, args, *, house: str = "",
                 house_digest: dict | None = None) -> _Watcher:
    """The watcher's harness: the gateway engine pinned to the model's
    verified providers, media held to WATCH_MAX_BYTES (in the engine's MiB),
    no `has`. The scorecard's reviewer is another harness, built as before."""
    return _Watcher(project.root, "qa", model=prepared.model, role=WATCH_ROLE, house=house,
                    house_digest=house_digest or {}, fresh=getattr(args, "fresh", False),
                    verbose=not getattr(args, "quiet", True), feedback=False,
                    timeout=WATCH_TIMEOUT, engine="gateway",
                    gateway={"providers": list(prepared.providers),
                             "max_media_mb": WATCH_MAX_BYTES / (1024 * 1024)})


def watch_prompt(title: str, seg: Segment, n: int, total: int, *, duration: float,
                 shots: list[dict], treatment: Any) -> str:
    """What the watcher is asked of one segment. Nothing in it names the
    pass, so the same bytes ask the same question and are served from
    decisions/qa/."""
    inside = []
    for shot in shots or []:
        try:
            at, secs = float(shot["at"]), float(shot.get("seconds") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if at < seg.end and at + secs > seg.start:
            inside.append({"label": shot.get("label"),
                           "from": round(max(at, seg.start) - seg.start, 3),
                           "to": round(min(at + secs, seg.end) - seg.start, 3)})
    scaled = (f", re-encoded at {seg.scale} lines with no sound" if seg.scale else
              ", the rendered file as it is")
    return "\n\n".join([
        f"Watch the attached video: part {n} of {total} of \"{title}\", the film from "
        f"{seg.start:g} s to {seg.end:g} s of its {duration:g} s ({seg.seconds:g} s{scaled}). "
        "File every problem a viewer would notice that the moving picture shows: its "
        f"`start` and `end` in seconds from the start of this video (0 to {seg.seconds:g}), "
        "its category, what is wrong, why it lowers quality, the owner (exactly one of those "
        "below) and the fix; `blocking` when a viewer would stop watching or stop trusting "
        "the film, else `minor`. Say in `watched_seconds` how long the video you watched "
        "was. What you could not see goes under not_evidenced.",
        brief("The shots in this video, in seconds from its start: context, not evidence",
              inside),
        brief("The film's treatment, as its styles declare it: context, not evidence",
              treatment),
        brief("Each finding's owner is exactly one of these; one you cannot place is "
              "`external`, with the reason in `fix`", {o: qa.DUTIES[o] for o in qa.OWNERS}),
    ])


def check(result: Any, seconds: float) -> tuple[list[dict], list[str]]:
    """The answer past the schema: (its findings, the problems as one line
    each). Every time inside the segment (SLACK past its end), and
    `watched_seconds` within max(2 s, 10 %) of its length: a provider that
    dropped the video answers from the prompt."""
    problems: list[str] = []
    if not isinstance(result, dict):
        return [], ["the answer is not an object"]
    watched = result.get("watched_seconds")
    if isinstance(watched, bool) or not isinstance(watched, (int, float)):
        problems.append(f"watched_seconds {watched!r} is not a number")
    elif abs(float(watched) - seconds) > max(2.0, 0.1 * seconds):
        problems.append(f"watched_seconds is {float(watched):g}, but the video lasts "
                        f"{seconds:g} s: watch the whole video")
    kept = []
    for i, row in enumerate(result.get("findings") or []):
        why = []
        if not isinstance(row, dict):
            problems.append(f"findings[{i}] is not a finding")
            continue
        start, end = row.get("start"), row.get("end")
        if not all(isinstance(t, (int, float)) and not isinstance(t, bool)
                   and math.isfinite(float(t)) for t in (start, end)):
            why.append(f"its start {start!r} and end {end!r} are not numbers of seconds")
        elif not 0 <= float(start) <= float(end) <= seconds + SLACK:
            why.append(f"its start {float(start):g} and end {float(end):g} are not inside the "
                       f"video (0 <= start <= end <= {seconds:g} s)")
        if row.get("category") not in CATEGORIES:
            why.append(f"its category {row.get('category')!r} is not one of "
                       f"{', '.join(CATEGORIES)}")
        if row.get("owner") not in qa.OWNERS:
            why.append(f"its owner {row.get('owner')!r} is not a stage")
        if row.get("severity") not in SEVERITIES:
            why.append(f"its severity {row.get('severity')!r} is not blocking or minor")
        if not str(row.get("problem") or "").strip():
            why.append("it names no problem")
        if why:
            problems.append(f"findings[{i}] ({str(row.get('problem') or '')[:80]!r}): "
                            + "; ".join(why))
        else:
            kept.append(dict(row, start=float(start), end=float(end)))
    return kept, problems


def merge_segments(per_segment: list[tuple[Segment, list[dict]]]) -> list[dict]:
    """Segment times shifted to film times; two findings of neighbouring
    segments that overlap, of one category, the later starting inside the
    overlap and both within SAME_SECONDS (the same moment, seen twice), kept
    once; ids W1.. in time order."""
    out: list[dict] = []
    previous: list[dict] = []
    ended = None                     # where the previous segment ends on the film
    for n, (seg, rows) in enumerate(per_segment, 1):
        mine = []
        # Only a cut inside a shot shares seconds with the piece before it;
        # at a shot boundary two findings either side are two moments.
        overlap = ended is not None and ended > seg.start
        for row in rows:
            f = dict(row, start=round(seg.start + float(row["start"]), 3),
                     end=round(seg.start + float(row["end"]), 3), segment=n)
            same = None if not overlap or f["start"] > ended else next(
                (p for p in previous if p["category"] == f["category"]
                 and abs(p["start"] - f["start"]) <= SAME_SECONDS), None)
            if same is not None:
                same.setdefault("segments", [same["segment"]]).append(n)
                if f.get("severity") == "blocking":
                    same["severity"] = "blocking"
                continue
            mine.append(f)
        out += mine
        previous, ended = mine, seg.end
    out.sort(key=lambda f: (f["start"], f["end"]))
    return [dict(f, id=f"W{i}") for i, f in enumerate(out, 1)]


def _budget_stop(exc: BaseException) -> bool:
    """A 402: the key's budget is spent, so nothing more is asked."""
    import gateway_engine
    seen: BaseException | None = exc
    while seen is not None:
        if getattr(seen, "status", None) == 402:
            return True
        seen = seen.__cause__
    low = str(exc).lower()
    return " 402" in low or any(m in low for m in gateway_engine.GATEWAY_PERMANENT_MARKERS)


def _earlier(home: Path, pass_dir: Path, want: dict) -> dict | None:
    """The latest earlier watch.json that watched every segment of this cut
    with this model, these providers, these constants and this role. A
    partial watch is never copied: a pass of the same cut asks again for
    what it lacks (the watched segments are served from decisions/qa/)."""
    for path in sorted((Path(home) / qa.QA_DIR).glob(f"pass-*/{RECORD}"), reverse=True):
        if path.parent == Path(pass_dir):
            continue
        old = read_json(path, warn=False)
        if not isinstance(old, dict):
            continue
        segments = (old.get("plan") or {}).get("segments") or []
        if (old.get("status") == "watched" and segments
                and all(s.get("status") == "watched" for s in segments)
                and (old.get("cut") or {}).get("sha256") == want["sha256"]
                and old.get("model") == want["model"]
                and list(old.get("providers") or []) == list(want["providers"])
                and old.get("constants") == want["constants"]
                and old.get("role_sha256") == want["role_sha256"]):
            return old
    return None


def watch(project, pass_dir: Path, prepared: Prepared, args, *, home: Path, build: dict,
          title: str, treatment: Any = None, house: str = "",
          house_digest: dict | None = None, harness: _Watcher | None = None) -> dict:
    """Plan, prepare, ask, check, record: the watch's record, also written
    to `pass_dir/watch.json`. A watch that cannot run is recorded with its
    reason and its not_evidenced line; it never stops the review."""
    pass_dir = Path(pass_dir)
    where = _where(project)
    base = {"pass": pass_dir.name, "model": prepared.model,
            "providers": list(prepared.providers), "catalog_row": prepared.row,
            "authors": prepared.authors, "reviewer_same_maker": prepared.reviewer_same_maker,
            "constants": constants(), "role_sha256": role_sha256(), "reused_from": None,
            "plan": None, "findings": [], "not_evidenced": [], "decisions": [],
            "calls": [], "engine": None}

    def done(status: str, reason: str = "", **more) -> dict:
        record = dict(base, status=status, reason=reason, **more)
        if reason and not record["not_evidenced"]:
            record["not_evidenced"] = [f"watch: {reason}"]
        write_json(pass_dir / RECORD, record)
        return record

    # -- the cut ---------------------------------------------------------------
    video = Path(home) / "video.mp4"
    problems = [] if video.is_file() else [
        (f"there is no {video.name} to watch", qa._yt("render", where))]
    problems = problems or doctor.cut_problems(project, build)
    if problems:
        return done("not_watched", "; ".join(text for text, _ in problems),
                    not_evidenced=[f"watch: {text}; run {command}" if command else
                                   f"watch: {text}" for text, command in problems])
    cut = {"path": str(video), "sha256": _sha256(video), "bytes": video.stat().st_size,
           "seconds": cut_seconds(project, build)}
    base["cut"] = cut
    if not getattr(args, "fresh", False):
        old = _earlier(home, pass_dir, {"sha256": cut["sha256"], "model": prepared.model,
                                        "providers": base["providers"],
                                        "constants": base["constants"],
                                        "role_sha256": base["role_sha256"]})
        if old is not None:
            kept = {k: old.get(k) for k in ("plan", "findings", "not_evidenced", "calls")}
            return done("watched", "", reused_from=old.get("pass"), **kept)

    # -- the plan, and the pieces ----------------------------------------------
    shots = (build or {}).get("shots") or []
    segments = watch_plan(cut["seconds"], cut["bytes"], shots, max_bytes=WATCH_MAX_BYTES,
                          max_seconds=WATCH_MAX_SECONDS, kbps=WATCH_KBPS)
    kind = plan_kind(segments)
    rows: list[dict] = []
    if kind == "as_is":
        rows = [{"n": 1, "path": str(video), "bytes": cut["bytes"], "seconds": cut["seconds"],
                 "audio": True}]
    else:
        try:
            prep = plan_remote(video, prepared.host, Path(project.root).name, segments,
                               pass_dir / SEGMENTS)
            rows = [dict(r, audio=False) for r in prepare_remote(prep)]
        except (WatchError, ValueError) as exc:
            base["plan"] = {"kind": kind, "segments": [
                {"n": n, "start": s.start, "end": s.end, "scale": s.scale}
                for n, s in enumerate(segments, 1)]}
            return done("failed", f"the cut could not be prepared to send, so nothing was "
                                  f"sent: {exc}")
    plan = {"kind": kind, "host": None if kind == "as_is" else prepared.host, "segments": []}
    for seg, row in zip(segments, rows):
        plan["segments"].append({"n": row["n"], "start": seg.start, "end": seg.end,
                                 "scale": seg.scale, "bytes": row["bytes"],
                                 "sha256": cut["sha256"] if kind == "as_is"
                                 else _sha256(Path(row["path"])),
                                 "audio": row["audio"], "file": row["path"],
                                 "status": "unwatched", "why": ""})
    base["plan"] = plan

    # -- the decisions -----------------------------------------------------------
    watcher = harness or make_harness(project, prepared, args, house=house,
                                      house_digest=house_digest)
    total = len(segments)
    per_segment: list[tuple[Segment, list[dict]]] = []
    noted: list[str] = []
    stopped = ""
    for n, (seg, entry) in enumerate(zip(segments, plan["segments"]), 1):
        name = f"watch-s{n:02d}"
        if stopped:
            entry["why"] = f"not asked: {stopped}"
            noted.append(f"watch: {seg.start:g}-{seg.end:g} s not watched ({entry['why']})")
            continue
        prompt = watch_prompt(title, seg, n, total, duration=cut["seconds"], shots=shots,
                              treatment=treatment)
        media = [entry["file"]]
        try:
            result = watcher.decide(name, prompt, WATCH_SCHEMA, media=media)
            kept, problems = check(result, seg.seconds)
            if problems:
                again = (prompt + "\n\nYour last answer cannot be kept: "
                         + "; ".join(problems[:10]) + ". Answer again, the whole answer: "
                         "watch the whole video, every time inside it.")
                result = watcher.decide(f"{name}-again", again, WATCH_SCHEMA, media=media)
                kept, problems = check(result, seg.seconds)
            if problems:
                # Both answers passed the schema, so the harness kept them:
                # kept, every rerun would be served them and fail the same
                # way until --fresh. A first answer whose again-answer is
                # kept stays: on a rerun both are replayed from the cache, at
                # no cost, to the same kept answer.
                watcher.forget(name)
                watcher.forget(f"{name}-again")
                entry["why"] = "its answer failed the checks twice: " + "; ".join(problems[:5])
                noted.append(f"watch: {seg.start:g}-{seg.end:g} s not watched ({entry['why']})")
                continue
        except HarnessError as exc:
            entry["why"] = str(exc)
            noted.append(f"watch: {seg.start:g}-{seg.end:g} s not watched ({exc})")
            if _budget_stop(exc):
                stopped = f"the gateway's budget stopped the watch at {name}"
            continue
        entry["status"], entry["why"] = "watched", ""
        noted += [f"watch: {seg.start:g}-{seg.end:g} s: {x}"
                  for x in result.get("not_evidenced") or [] if str(x).strip()]
        per_segment.append((seg, kept))
    findings = merge_segments(per_segment)
    report = watcher.report()
    watched = sum(1 for e in plan["segments"] if e["status"] == "watched")
    status = "watched" if watched else "failed"
    reason = "" if watched == total else (
        f"{total - watched} of {total} segment(s) not watched")
    return done(status, reason, findings=findings, not_evidenced=noted,
                decisions=report["decisions"], calls=watcher.calls(), engine=report)


# --------------------------------------------------------------------------- #
# what the pass takes from it
# --------------------------------------------------------------------------- #

def candidates(record: dict | None) -> list[dict]:
    """Each watched finding as a sampling candidate for qa.sample: its id,
    its start, its severity and the first words of its problem."""
    return [{"id": f["id"], "t": float(f["start"]), "severity": f.get("severity"),
             "problem": str(f.get("problem") or "")}
            for f in (record or {}).get("findings") or [] if isinstance(f, dict)]


def stills_for(samples: list[dict]) -> dict[str, list[dict]]:
    """Each watched id with the sample rows that show it."""
    out: dict[str, list[dict]] = {}
    for row in samples or []:
        for wid in row.get("watched") or []:
            out.setdefault(wid, []).append(row)
    return out


def asks(record: dict | None, samples: list[dict]) -> bool:
    """Whether the scorecard asks the reviewer about watched findings: one
    of them has a still."""
    return bool(record and record.get("findings") and stills_for(samples))


def brief_for(record: dict, samples: list[dict]) -> str:
    """What the scorecard's reviewer is told of the watched findings."""
    shown = stills_for(samples)
    rows = []
    for f in record.get("findings") or []:
        if f["id"] not in shown:
            continue
        frames = [r["frame"] for r in shown[f["id"]]]
        rows.append({"id": f["id"], "start": f["start"], "end": f["end"],
                     "category": f.get("category"), "problem": f.get("problem"),
                     "severity": f.get("severity"), "frames": frames,
                     "burst": [x for r in shown[f["id"]]
                               for x in (r.get("burst") or {}).get("files") or []]})
    return "\n\n".join([
        brief("Watched findings: a model's observations at its own sampling, not "
              f"measurements ({record.get('model')} watched the cut)", rows),
        "For each watched finding above, answer under `watched`: its `id`; `confirmed` only "
        "when its still or a frame of its burst shows the problem, with the file that shows "
        "it in `frame`, written as the file name as listed in `frames` or `burst` above (the "
        "file is under `frames/`); `not_visible` when none of them shows it; `disagree` when "
        "they show it is not a problem; and `why`. A watched finding is not yours: do not file "
        "it again among your findings.",
    ])


def frame_named(said: Any) -> str:
    """The frame a reviewer named, as the samples list it. The reviewer
    works in the pass directory, where a still is `frames/<name>`, so that
    path (or `./frames/<name>`) names the same file as `<name>`."""
    return re.sub(r"^(\./)?frames/", "", str(said or "").strip())


def verdicts(record: dict, readings: list[dict], samples: list[dict],
             frames_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """(confirmed, not confirmed, not sampled). A finding is confirmed when
    at least two valid scoring samples confirm it naming a frame of its own
    still or burst that exists under frames/; a confirmation without one is
    not_visible. Each row carries the votes."""
    shown = stills_for(samples)
    valid = [r for r in readings or [] if r.get("valid")]
    confirmed, unconfirmed, unsampled = [], [], []
    for f in record.get("findings") or []:
        wid = f["id"]
        if wid not in shown:
            unsampled.append(f)
            continue
        own = {r["frame"] for r in shown[wid]} | {
            x for r in shown[wid] for x in (r.get("burst") or {}).get("files") or []}
        votes, yes, frame, why = [], 0, None, ""
        for r in valid:
            said = next((w for w in (r.get("card") or {}).get("watched") or []
                         if isinstance(w, dict) and w.get("id") == wid), None)
            verdict = (said or {}).get("verdict") or "no answer"
            named = frame_named((said or {}).get("frame"))
            if verdict == "confirmed" and not (named in own and (Path(frames_dir) / named).is_file()):
                verdict = "not_visible"           # a confirmation without its still
            votes.append({"k": r.get("k"), "verdict": verdict, "frame": named,
                          "why": str((said or {}).get("why") or "")})
            if verdict == "confirmed":
                yes += 1
                frame, why = frame or named, why or str(said.get("why") or "")
        row = dict(f, votes=votes)
        if len(valid) >= 2 and yes >= 2:
            confirmed.append(dict(row, frame=frame, confirmed_why=why))
        else:
            unconfirmed.append(row)
    return confirmed, unconfirmed, unsampled


def merge_into(card: dict, record: dict, readings: list[dict], samples: list[dict],
               frames_dir: Path) -> dict:
    """The scorecard with the confirmed watched findings among its own
    (`source: watched:Wn`, routed and blocking like any other), the rest
    listed under `watched`, and what was not watched under not_evidenced."""
    card = dict(card)
    confirmed, unconfirmed, unsampled = verdicts(record, readings, samples, frames_dir)
    added = [{"timestamp": f["start"], "frame": f["frame"], "problem": f.get("problem"),
              "why": f.get("why"),
              "root_cause": f"seen in motion by {record.get('model')} ({f['start']:g}-"
                            f"{f['end']:g} s) and confirmed on its still: "
                            f"{f.get('confirmed_why', '')}",
              "owner": f.get("owner"), "fix": f.get("fix"), "severity": f.get("severity"),
              "category": f.get("category"), "source": f"watched:{f['id']}"}
             for f in confirmed]
    card["findings"] = list(card.get("findings") or []) + added
    card["not_evidenced"] = list(card.get("not_evidenced") or []) + list(
        record.get("not_evidenced") or [])
    card["watched"] = {"confirmed": [f["id"] for f in confirmed],
                       "not_confirmed": [{k: f.get(k) for k in (
                           "id", "start", "end", "category", "problem", "severity", "votes")}
                           for f in unconfirmed],
                       "not_sampled": [{k: f.get(k) for k in (
                           "id", "start", "end", "category", "problem", "severity")}
                           for f in unsampled]}
    return card


def card_entry(record: dict) -> dict:
    """The scorecard's `watch`: whether it watched, who, the plan and what it cost."""
    return {k: record.get(k) for k in ("status", "reason", "model", "providers",
                                       "reused_from", "reviewer_same_maker", "decisions",
                                       "calls")} | {
        "cut_sha256": (record.get("cut") or {}).get("sha256"),
        "plan": (record.get("plan") or {}).get("kind"),
        "findings": len(record.get("findings") or []), "file": RECORD}


def findings_lines(card: dict) -> list[str]:
    """findings.md's section on the watch."""
    entry, said = card.get("watch") or {}, card.get("watched") or {}
    lines = ["## Watched", "",
             f"{entry.get('model')} watched the cut ({entry.get('status')}"
             + (f": {entry.get('reason')}" if entry.get("reason") else "")
             + (f", reused from {entry.get('reused_from')}" if entry.get("reused_from") else "")
             + "). A watched finding counts only when two scoring samples confirm it on "
             "its still.", ""]
    if said.get("confirmed"):
        lines.append(f"- confirmed, among the findings: {', '.join(said['confirmed'])}")
    for f in said.get("not_confirmed") or []:
        votes = ", ".join(f"{v.get('verdict')}" + (f" ({v.get('why')})" if v.get("why") else "")
                          for v in f.get("votes") or []) or "no valid sample"
        lines.append(f"- watched, not confirmed: {f.get('id')} {qa._stamp(f.get('start'))} "
                     f"[{f.get('severity')}] {f.get('problem')} -- votes: {votes}")
    for f in said.get("not_sampled") or []:
        lines.append(f"- watched, not sampled: {f.get('id')} {qa._stamp(f.get('start'))} "
                     f"[{f.get('severity')}] {f.get('problem')}")
    return lines + [""]


# --------------------------------------------------------------------------- #
# the estimate
# --------------------------------------------------------------------------- #

def _flag(argv: list[str], name: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def watch_usd(root: Path | None, argv=()) -> float:
    """What `yt qa --watch` adds: the programme's seconds at
    WATCH_TOKENS_PER_SECOND at the watcher's pinned input price, plus
    WATCH_OUTPUT_TOKENS a segment at its output price; 0 for a model with
    no pinned price. Never fetches."""
    argv = [str(a) for a in argv or ()]
    model = _flag(argv, "--watch-model") or WATCH_MODEL
    price = WATCH_PRICE.get(model)
    if not price:
        return 0.0
    build = read_json(Path(root) / "build.json", warn=False) if root is not None else None
    try:
        seconds = float(((build or {}).get("scene") or {}).get("duration"))
    except (TypeError, ValueError, AttributeError):
        seconds = float(ASSUMED_SECONDS)
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = float(ASSUMED_SECONDS)
    cap = split_cap(max_bytes=WATCH_MAX_BYTES, max_seconds=WATCH_MAX_SECONDS, kbps=WATCH_KBPS)
    pieces = max(1, math.ceil(seconds / cap))
    return round((seconds * WATCH_TOKENS_PER_SECOND * price["input"]
                  + WATCH_OUTPUT_TOKENS * pieces * price["output"]) / 1_000_000, 4)
