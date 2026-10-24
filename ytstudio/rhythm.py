"""The film's rhythm, measured (AUTO-141): holds under narration, the
opening's change rate, how shot lengths spread, and the words a minute of
each section.

A model judging editing_pacing from stills cannot see a picture that sits
still for twelve seconds while the narrator talks, or cuts that land every
four seconds like a metronome. Direction 12 gives the check (the longest
interval with no visual change while narration runs, over 8 s; an opening
30 s changing slower than the film's median; a coefficient of variation of
shot length under 0.3, counting cuts or builds) and direction 25 gives the
delivery band (words over voiced seconds, silences over 1 s left out:
fail under 130 or over 200, warn outside 150-190). This module holds those
measures, pure, and the one remote scan that gives the render's side of
them. `yt qa` files what they find beside its other mechanical findings,
and `yt voice` shows the words a minute per section before a render.

Two sources. The timeline (build.json's shots, voice.json's takes) always
answers: the shot-length spread and the words a minute. The render answers
the rest: ffmpeg's scene score per frame, at SCAN_FPS on a SCAN_WIDTH copy,
read on a render host (invariant 9), never here. Its thresholds are not yet
calibrated against real renders, so until RHYTHM["calibrated"] is True a
finding that rests on CHANGE_SCORE is filed minor and says TO VERIFY: an
unmeasured threshold never blocks a film.

It imports render and script only, never qa or voice: voice imports it.
"""

from __future__ import annotations

import re
import shlex
import statistics
import subprocess
import time
from pathlib import Path

from . import render, script

#: The thresholds, shaped like script.BOUNDS: the value, what it counts,
#: where the corpus says it, how strong that source is, and why.
RHYTHM: dict[str, dict] = {
    "hold_s": {
        "value": 8.0, "unit": "seconds with no visual change while narration runs",
        "corpus": ("direction 12",), "strength": "house",
        "note": "direction 12 calls its 8 s figure a house bound; a hold is filed on "
                "the part of it the narration overlaps"},
    "narration_gap_s": {
        "value": 1.5, "unit": "seconds of silence that still count as the narration "
                              "running", "corpus": ("direction 25",), "strength": "house",
        "note": "direction 25 reads a pause over 1.5 s as a deliberate beat; a shorter "
                "one is the voice between words, not the voice stopping"},
    "cv_floor": {
        "value": 0.3, "unit": "coefficient of variation of the intervals between cuts "
                              "or builds", "corpus": ("direction 12",), "strength": "house",
        "note": "the corpus says flag; blocking is a house decision"},
    "opening_s": {
        "value": 30.0, "unit": "seconds from the top whose change rate is compared with "
                               "the film's median 30 s window", "corpus": ("direction 12",),
        "strength": "house", "note": "the corpus says flag; blocking is a house decision"},
    "wpm_fail": {
        "value": (130, 200), "unit": "words a minute of voiced time, per section",
        "corpus": ("direction 25",), "strength": "data study",
        "note": "Rodero 2016: 150 rated slow, 170-190 recalled best, 210 fast"},
    "wpm_warn": {
        "value": (150, 190), "unit": "words a minute of voiced time, per section",
        "corpus": ("direction 25",), "strength": "data study"},
    "wpm_gap_s": {
        "value": 1.0, "unit": "seconds of silence between words left out of the voiced "
                              "time", "corpus": ("direction 25",), "strength": "data study"},
    "onset_quiet_s": {
        "value": 1.0, "unit": "seconds under CHANGE_SCORE before a change counts as a "
                              "build of its own", "corpus": ("direction 12",),
        "strength": "house",
        "note": "a build is a change after the picture settled, not one frame of a "
                "continuous move"},
    "cut_merge_s": {
        "value": 0.5, "unit": "seconds within which a render onset is the authored cut",
        "corpus": ("direction 12",), "strength": "house"},
    "scan_fps": {
        "value": 4, "unit": "frames a second the scan scores", "corpus": ("direction 12",),
        "strength": "TO VERIFY",
        "note": "the scan time of a 10-minute 1080p cut at this rate is not yet measured; "
                "rhythm.json records it on every run"},
    "scan_width": {
        "value": 320, "unit": "pixels wide the scan scores at", "corpus": ("direction 12",),
        "strength": "TO VERIFY", "note": "QA's thumbnail width (qa.THUMB)"},
    "change_score": {
        "value": 0.004, "unit": "ffmpeg scene score at or over which a frame changed",
        "corpus": ("direction 12",), "strength": "TO VERIFY",
        "note": "a guess until two existing renders are scanned on the render host and a "
                "slow camera drift is seen to count as change while a held still does not"},
    "cut_score": {
        "value": 0.3, "unit": "ffmpeg scene score at or over which a frame is a hard cut",
        "corpus": ("direction 12",), "strength": "TO VERIFY",
        "note": "ffmpeg's usual scene-cut threshold; counted beside the authored cuts in "
                "rhythm.json's scan block so the calibration run can compare them"},
    "calibrated": {
        "value": False, "unit": "whether CHANGE_SCORE and CUT_SCORE are measured",
        "corpus": ("direction 12",), "strength": "TO VERIFY",
        "note": "False until the calibration run (two existing renders scanned on the "
                "render host: a slow camera drift must count as change, a held still must "
                "not) is recorded in the change_score and cut_score notes, in the commit "
                "that turns this True. Until then a hold, a slow opening and an "
                "events-basis metronomic finding are filed minor"},
}

#: The kinds this module files, in qa's mechanical shape.
KINDS = ("hold", "metronomic", "slow-opening", "wpm")
#: The kinds that rest on the render's scene scores: minor until calibrated.
SCANNED = ("hold", "slow-opening")
#: Where `yt qa` keeps a pass's measurements, beside scorecard.json.
RECORD = "rhythm.json"
#: How long the cut may take to go up, and the scan to run, on the host.
UPLOAD_TIMEOUT, SCAN_TIMEOUT = 1800, 1800
#: Timings by how much of the read they evidence: a section takes its least.
TIMINGS = ("exact", "aligned", "estimated", "none")
#: The timings whose word times are measured, so pauses can be left out.
MEASURED = ("exact", "aligned")
UNCALIBRATED = "(uncalibrated scan: TO VERIFY)"
PAUSES_UNKNOWN = "pauses cannot be removed without aligned timings"


class RhythmError(RuntimeError):
    """The scan could not be made: no host, YT_NO_REMOTE, no ffmpeg, a dead link."""


def value(key: str):
    """A threshold's value, read when it is used, so a test can patch RHYTHM."""
    return RHYTHM[key]["value"]


def calibrated() -> bool:
    return bool(value("calibrated"))


# --------------------------------------------------------------------------- #
# the timeline
# --------------------------------------------------------------------------- #

def _shots(build: dict | None) -> list[dict]:
    """build.json's shots with a length, in film order."""
    out = []
    for shot in (build or {}).get("shots") or []:
        try:
            at, seconds = float(shot.get("at") or 0), float(shot.get("seconds") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if seconds > 0:
            out.append(dict(shot, at=at, seconds=seconds))
    return sorted(out, key=lambda s: s["at"])


def duration_of(build: dict | None) -> float:
    """The programme's length: the scene's duration, else the last shot's end."""
    try:
        seconds = float(((build or {}).get("scene") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    shots = _shots(build)
    return seconds or (max(s["at"] + s["seconds"] for s in shots) if shots else 0.0)


def shot_lengths(build: dict | None) -> list[float]:
    """Each shot's seconds in film order; a shot of 0 s is no shot."""
    return [round(s["seconds"], 3) for s in _shots(build)]


def edge_labels(build: dict | None, opening_s: float | None = None) -> set[str]:
    """The shots a single defect blocks on: every shot overlapping the
    opening, the last shot, and every shot whose screenplay `function` is
    hook or payoff. Read from build.json alone, so a shot no still sampled
    is still an edge (qa._on_edge reads only sampled rows)."""
    opening = value("opening_s") if opening_s is None else float(opening_s)
    shots = _shots(build)
    out = {str(s.get("label", "")) for s in shots if s["at"] < opening}
    if shots:
        out.add(str(shots[-1].get("label", "")))
    out |= {str(s.get("label", "")) for s in shots if s.get("function") in ("hook", "payoff")}
    return out


def _shot_at(shots: list[dict], t: float) -> dict | None:
    """The shot on screen at `t`, else the last one that started before it."""
    before = None
    for shot in shots:
        if shot["at"] <= t < shot["at"] + shot["seconds"]:
            return shot
        if shot["at"] <= t:
            before = shot
    return before or (shots[0] if shots else None)


def narrated_spans(build: dict | None, voice: dict | None) -> list[tuple[float, float]]:
    """Film-time [start, end] of every spoken word: a take's place (its
    shot's `at` in build.json, else its own) plus each word's start and end
    in the take; a take with no words is its whole [at, at + seconds]."""
    if not voice or voice.get("wordless"):
        return []
    placed = {str(s.get("label", "")): s["at"] for s in _shots(build)}
    out = []
    for take in voice.get("takes") or []:
        if not isinstance(take, dict):
            continue
        try:
            at = float(placed.get(str(take.get("label", "")), take.get("at") or 0))
            seconds = float(take.get("seconds") or 0)
        except (TypeError, ValueError):
            continue
        words = []
        for w in take.get("words") or []:
            try:
                words.append((at + float(w["start"]), at + float(w["end"])))
            except (KeyError, TypeError, ValueError):
                continue
        if words:
            out += [(round(a, 3), round(b, 3)) for a, b in words if b >= a]
        elif seconds > 0:
            out.append((round(at, 3), round(at + seconds, 3)))
    return sorted(out)


def _merged(spans: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    """Spans joined where the silence between them is at most `gap`."""
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


# --------------------------------------------------------------------------- #
# the render's scene scores
# --------------------------------------------------------------------------- #

def holds(scores, fps: float) -> list[tuple[float, float]]:
    """Every run of frames all under CHANGE_SCORE, as (start, end): a
    frame's score compares it with the one before, so the picture held from
    the frame before the run's first to the run's last."""
    threshold, step = value("change_score"), 1.0 / float(fps)
    out, first, last = [], None, None
    for t, score in scores or []:
        if score < threshold:
            first = t if first is None else first
            last = t
            continue
        if first is not None:
            out.append((round(max(0.0, first - step), 3), round(last, 3)))
        first = last = None
    if first is not None:
        out.append((round(max(0.0, first - step), 3), round(last, 3)))
    return out


def holds_under_narration(found, spans, hold_s: float | None = None
                          ) -> list[tuple[float, float]]:
    """The part of each hold the narration overlaps, where that part runs
    longer than `hold_s`. The narration is its words joined across pauses of
    up to narration_gap_s: the voice between words has not stopped."""
    limit = value("hold_s") if hold_s is None else float(hold_s)
    voiced = _merged(spans or [], value("narration_gap_s"))
    out = []
    for a, b in found or []:
        for c, d in voiced:
            lo, hi = max(a, c), min(b, d)
            if hi - lo > limit:
                out.append((round(lo, 3), round(hi, 3)))
    return out


def change_events(build: dict | None, scores, fps: float) -> list[float]:
    """Each visual change: every authored cut (a shot's `at` after the
    first) and every render onset, a frame at or over CHANGE_SCORE after at
    least onset_quiet_s under it, so a build inside a long shot counts as
    direction 12 says. An onset within cut_merge_s of an authored cut is
    that cut."""
    cuts = sorted({round(s["at"], 3) for s in _shots(build) if s["at"] > 0})
    threshold, quiet_s = value("change_score"), value("onset_quiet_s")
    merge = value("cut_merge_s")
    onsets, quiet_from = [], None
    for t, score in scores or []:
        if score < threshold:
            quiet_from = t if quiet_from is None else quiet_from
            continue
        if quiet_from is not None and t - quiet_from >= quiet_s - 1e-9 and t > 0:
            if not any(abs(t - c) <= merge for c in cuts):
                onsets.append(round(t, 3))
        quiet_from = None
    return sorted(set(cuts) | set(onsets))


def intervals_of(events: list[float], duration: float) -> list[float]:
    """The intervals between consecutive changes, the film's start and end
    the first and last bound: with no onset, the shot lengths."""
    bounds = [0.0] + [e for e in sorted(events) if 0 < e < duration] + [duration]
    return [round(b - a, 3) for a, b in zip(bounds, bounds[1:]) if b - a > 0]


def interval_cv(intervals) -> float | None:
    """The population standard deviation over the mean; None for fewer than
    four intervals, which evidence no spread."""
    values = [float(v) for v in intervals or [] if float(v) > 0]
    if len(values) < 4:
        return None
    mean = sum(values) / len(values)
    return round(statistics.pstdev(values) / mean, 3) if mean else None


def opening_rate(events, duration: float, opening_s: float | None = None) -> float | None:
    """Changes a minute in [0, opening_s); None for a film shorter than that."""
    opening = value("opening_s") if opening_s is None else float(opening_s)
    if duration < opening:
        return None
    count = sum(1 for e in events or [] if 0 < e < opening)
    return round(count * 60.0 / opening, 2)


def film_median_rate(events, duration: float, window: float | None = None) -> float | None:
    """The median of the changes a minute over consecutive full windows of
    opening_s, the opening's own among them; None for a film under three."""
    size = value("opening_s") if window is None else float(window)
    full = int(duration // size) if size else 0
    if full < 3:
        return None
    rates = [sum(1 for e in events or [] if k * size <= e < (k + 1) * size) * 60.0 / size
             for k in range(full)]
    return round(statistics.median(rates), 2)


_PTS = re.compile(r"\bpts_time:\s*(-?[0-9.]+)")
_SCORE = re.compile(r"\blavfi\.scene_score=\s*(-?[0-9.]+)")


def parse_scene_scores(text: str) -> list[tuple[float, float]]:
    """(t, score) pairs out of ffmpeg's `metadata=print` log: each
    `pts_time:` line with the `lavfi.scene_score=` that follows it. Every
    other line (the banner, the progress) is ignored."""
    out, pending = [], None
    for line in (text or "").splitlines():
        at = _PTS.search(line)
        if at:
            pending = float(at.group(1))
            continue
        score = _SCORE.search(line)
        if score and pending is not None:
            out.append((round(pending, 3), round(float(score.group(1)), 6)))
            pending = None
    return out


def scan_command(cut: str, ffmpeg: str = "") -> str:
    """The one command the host runs: ffmpeg on PATH, else beside its
    ffprobe, scoring every frame of the cut at scan_fps and scan_width with
    no audio, the scores printed to stderr, nothing written."""
    q = shlex.quote
    vf = (f"fps={value('scan_fps')},scale={value('scan_width')}:-2,"
          "select='gte(scene,0)',metadata=print:key=lavfi.scene_score")
    return (f'F=$(command -v ffmpeg 2>/dev/null); [ -n "$F" ] || F={q(ffmpeg)}; '
            f'if [ -x "$F" ]; then "$F" -nostdin -hide_banner -i {q(cut)} -an -vf "{vf}" '
            '-f null -; else echo __NO_FFMPEG__; false; fi')


def _host_ffmpeg(host: str) -> str:
    """ffmpeg beside the host's ffprobe, as its probe found it; "" when it did not say."""
    known = render._HOSTS.get(host)
    if known is None:
        try:
            known = render.probe(host)
        except Exception:                                    # noqa: BLE001
            return ""
    probe = str(getattr(known, "ffprobe", "") or "")
    return f"{Path(probe).parent}/ffmpeg" if probe else ""


def scan_remote(cut, args, *, slug: str = "qa",
                remote_cut: tuple[str, str] | None = None) -> dict:
    """The cut's scene score per frame, scored on a render host. The cut
    goes up once (rsync streams it from disk), one ssh runs ffmpeg, the
    scores come back on stderr, and the host's directory is removed
    whatever happened. `remote_cut` (host, path) is a cut already on a host,
    scored there with no upload of its own. Returns {"scores": [(t, s)],
    "scan": {host, fps, width, frames, upload, upload_seconds, upload_bytes,
    scan_seconds, render_cuts}}. RhythmError when nothing can be scanned:
    under YT_NO_REMOTE nothing is launched at all."""
    cut = Path(cut)
    try:
        render.refuse_remote("rsync")
    except render.RemoteRefused as exc:
        raise RhythmError(str(exc)) from exc
    host = remote_cut[0] if remote_cut else render.remote_host(args, "validate")
    if not host:
        raise RhythmError("no render host configured (RS_RENDER_HOSTS or RS_RENDER_HOST); "
                          "ffmpeg is not run on this workstation")
    root = render.remote_dir(slug, "rhythm", render.job_tag())
    sent = False
    try:
        ffmpeg = _host_ffmpeg(host)
        if remote_cut:
            path, upload = remote_cut[1], {"upload": "reused", "upload_seconds": 0.0,
                                           "upload_bytes": 0}
        else:
            path = f"{root}/cut.mp4"
            started, sent = time.monotonic(), True
            up = subprocess.run(render.sync_up_args(cut, host, path, file=True, delete=False,
                                                    excludes=()),
                                capture_output=True, text=True, timeout=UPLOAD_TIMEOUT)
            if up.returncode != 0:
                raise RhythmError(f"the cut could not be sent to {host}: "
                                  f"{(up.stderr or '').strip()[-300:]}")
            upload = {"upload": "sent",
                      "upload_seconds": round(time.monotonic() - started, 2),
                      "upload_bytes": cut.stat().st_size}
        render.refuse_remote()
        started = time.monotonic()
        done = subprocess.run(render.ssh_argv(host, scan_command(path, ffmpeg)),
                              capture_output=True, text=True, timeout=SCAN_TIMEOUT)
        if "__NO_FFMPEG__" in (done.stdout or ""):
            raise RhythmError(f"no ffmpeg on render host {host} (not on PATH, not beside "
                              "its ffprobe)")
        scores = parse_scene_scores(done.stderr or "")
        if not scores:
            raise RhythmError(f"the scan on {host} returned no scene scores (exit "
                              f"{done.returncode}): {(done.stderr or '').strip()[-200:]}")
    except (render.RemoteRefused, subprocess.TimeoutExpired, OSError) as exc:
        raise RhythmError(f"render host {host}: {exc}") from exc
    finally:
        if sent:
            try:
                render.remove_remote(host, root)
            except (render.RemoteRefused, OSError, ValueError):
                pass
    return {"scores": scores, "scan": {
        "host": host, "fps": value("scan_fps"), "width": value("scan_width"),
        "frames": len(scores), **upload,
        "scan_seconds": round(time.monotonic() - started, 2),
        "render_cuts": sum(1 for _, s in scores if s >= value("cut_score"))}}


# --------------------------------------------------------------------------- #
# words a minute
# --------------------------------------------------------------------------- #

def _take_words(take: dict) -> list[tuple[float, float]]:
    out = []
    for w in take.get("words") or []:
        try:
            if isinstance(w, dict):
                out.append((float(w["start"]), float(w["end"])))
            else:
                out.append((float(w.start), float(w.end)))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return sorted(out)


def section_wpm(takes) -> list[dict]:
    """Words a minute per section, in the order the sections are read:
    words over voiced seconds, an inter-word silence over wpm_gap_s left
    out (direction 25). Each row carries `timing`, the least exact of its
    takes (TIMINGS). An estimated take or one with no word timings spreads
    its words over the whole take, so its pauses stay in and its rate is
    understated. Takes are voice.json's dicts (section, words, seconds,
    timing, text)."""
    gap_s = value("wpm_gap_s")
    rows: dict[str, dict] = {}
    for take in takes or []:
        if not isinstance(take, dict):
            continue
        section = str(take.get("section", ""))
        row = rows.setdefault(section, {"section": section, "takes": 0, "words": 0,
                                        "voiced_seconds": 0.0, "left_out_seconds": 0.0,
                                        "timing": "exact"})
        timing = str(take.get("timing") or "none")
        timing = timing if timing in TIMINGS else "none"
        words = _take_words(take)
        try:
            seconds = float(take.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        count = len(take.get("words") or []) or len(str(take.get("text", "")).split())
        if timing in MEASURED and words:
            span = words[-1][1] - words[0][0]
            gaps = sum(b[0] - a[1] for a, b in zip(words, words[1:]) if b[0] - a[1] > gap_s)
            row["voiced_seconds"] += max(0.0, span - gaps)
            row["left_out_seconds"] += gaps
        else:
            timing = timing if timing not in MEASURED else "none"
            row["voiced_seconds"] += seconds
        row["takes"] += 1
        row["words"] += count
        if TIMINGS.index(timing) > TIMINGS.index(row["timing"]):
            row["timing"] = timing
    for row in rows.values():
        row["voiced_seconds"] = round(row["voiced_seconds"], 3)
        row["left_out_seconds"] = round(row["left_out_seconds"], 3)
        row["wpm"] = (round(row["words"] * 60.0 / row["voiced_seconds"], 1)
                      if row["voiced_seconds"] > 0 and row["words"] else None)
    return list(rows.values())


# --------------------------------------------------------------------------- #
# the findings and the record
# --------------------------------------------------------------------------- #

def _finding(kind: str, shot: dict | None, *, timestamp: float, scope: str, problem: str,
             why: str, root_cause: str, fix: str, owner: str, severity: str, **more) -> dict:
    return {"timestamp": round(float(timestamp), 3), "frame": "",
            "shot": str((shot or {}).get("label", "")), "kind": kind, "scope": scope,
            "problem": problem, "why": why, "root_cause": root_cause, "fix": fix,
            "owner": owner, "severity": severity, **more}


def _gate(severity: str, why: str) -> tuple[str, str]:
    """A finding that rests on CHANGE_SCORE is minor until the scan is calibrated."""
    if calibrated():
        return severity, why
    return "minor", f"{why} {UNCALIBRATED}"


def measure(build: dict | None, takes, scores=None, *, fps: float | None = None,
            edge: set | None = None, scan: dict | None = None,
            unscanned: tuple[str, str] | None = None) -> dict:
    """The rhythm block of one pass: every measure, the findings it files
    (qa's mechanical shape plus `scope`, and `basis` or `section`), and what
    it could not evidence. `scores` is the render's (t, score) series, or
    None when there was no scan; `unscanned` is then (why, command), named
    under not_evidenced for the hold and the opening. Nothing missing is
    scored as a pass or as a failure."""
    fps = float(fps or value("scan_fps"))
    shots, duration = _shots(build), duration_of(build)
    first = shots[0] if shots else None
    edge = set(edge) if edge is not None else edge_labels(build)
    takes = [t for t in takes or [] if isinstance(t, dict)]
    scanned = scores is not None
    found: list[dict] = []
    not_evidenced: list[dict] = []
    floor = value("cv_floor")

    # -- the spread of cuts and builds -------------------------------------
    lengths = shot_lengths(build)
    cv_shots = interval_cv(lengths)
    events = change_events(build, scores, fps) if scanned else []
    gaps = intervals_of(events, duration) if scanned else []
    cv_events = interval_cv(gaps) if scanned else None
    basis = "events" if scanned else "shots"

    def metronomic(cv: float, count: int, on: str, severity: str, why: str) -> dict:
        return _finding(
            "metronomic", first, timestamp=0.0, scope="film", basis=on,
            problem=f"cuts and builds vary by CV {cv:.2f} (< {floor:g}) over {count} "
                    f"intervals (basis {on})",
            why=why, owner="vfx" if on == "events" else "script", severity=severity,
            root_cause=("every section holds its pictures for about the same time, so the "
                        "film cuts like a metronome whatever it says"),
            fix=("vary the beats: more cuts or builds where the passage is dense, fewer "
                 "where it is simple" + (" (re-plan the shots' durations)" if on == "shots"
                                         else " (builds inside the long shots)")))
    told = ("direction 12: shot length that does not vary with the information reads "
            "as a slideshow; the corpus says flag, blocking is a house decision")
    if scanned and cv_events is not None and calibrated():
        if cv_events < floor:
            found.append(metronomic(cv_events, len(gaps), "events", "blocking", told))
    elif cv_shots is not None and cv_shots < floor:
        if scanned and cv_events is not None and cv_events >= floor:
            found.append(metronomic(
                cv_shots, len(lengths), "shots", "minor",
                f"{told}; the shots vary by CV {cv_shots:.2f} but the uncalibrated scan's "
                f"cuts and builds vary by CV {cv_events:.2f} {UNCALIBRATED}"))
        else:
            seen = (f"; the scan's cuts and builds vary by CV {cv_events:.2f}"
                    if scanned and cv_events is not None else "")
            found.append(metronomic(cv_shots, len(lengths), "shots", "blocking",
                                    f"{told}{seen}; measured from build.json"))
    elif scanned and cv_events is not None and cv_events < floor:
        severity, why = _gate("blocking", told)
        found.append(metronomic(cv_events, len(gaps), "events", severity, why))

    # -- holds under narration and the opening -----------------------------
    longest, under = None, []
    rate = median = None
    if scanned:
        under = holds_under_narration(holds(scores, fps), narrated_spans(build, {"takes": takes}))
        held_on = {str((_shot_at(shots, a) or {}).get("label", "")) for a, _ in under}
        for a, b in under:
            shot = _shot_at(shots, a)
            label = str((shot or {}).get("label", ""))
            severity, why = _gate(
                "blocking" if label in edge or len(held_on) > 1 else "minor",
                "a picture that does not change while the voice goes on reads as a slide "
                f"(direction 12, over {value('hold_s'):g} s); measured from the render's "
                "scene scores, not judged")
            found.append(_finding(
                "hold", shot, timestamp=a, scope="shot", severity=severity, why=why,
                problem=f"no visual change for {b - a:.1f} s ({a:.1f}-{b:.1f} s) while "
                        "the narration runs",
                owner="vfx" if (shot or {}).get("asset") == "effect" else "build",
                root_cause="nothing is animated across this span: the camera, the "
                           "objects and the lights all hold while the line is said",
                fix="build the picture with the line (a reveal, a move, a change of "
                    "state), or cut to the next idea sooner"))
        if under:
            a, b = max(under, key=lambda span: span[1] - span[0])
            longest = {"seconds": round(b - a, 3), "start": a, "end": b,
                       "shot": str((_shot_at(shots, a) or {}).get("label", ""))}
        rate, median = opening_rate(events, duration), film_median_rate(events, duration)
        if rate is not None and median is not None and rate < median:
            severity, why = _gate(
                "blocking", "the first 30 s decide whether a viewer stays; an opening "
                "that changes less often than the rest of the film asks for patience "
                "before it has earned any (direction 12; the corpus says flag, blocking "
                "is a house decision)")
            found.append(_finding(
                "slow-opening", first, timestamp=0.0, scope="film", severity=severity,
                why=why, owner="vfx",
                problem=f"opening {value('opening_s'):g} s has {rate:.1f} changes/min "
                        f"against a film median of {median:.1f}",
                root_cause="the opening holds its first pictures longer than the film "
                           "does anywhere else",
                fix="cut or build more often in the opening, at least as often as the "
                    "film's typical 30 s"))
    elif unscanned:
        why, command = unscanned
        for kind in SCANNED:
            not_evidenced.append({"measure": kind, "why": why, "command": command})

    # -- words a minute ------------------------------------------------------
    sections = section_wpm(takes)
    (fail_lo, fail_hi), (warn_lo, warn_hi) = value("wpm_fail"), value("wpm_warn")
    for row in sections:
        wpm = row["wpm"]
        if wpm is None:
            continue
        fast, slow = wpm > warn_hi, wpm < warn_lo
        if not (fast or slow):
            continue
        if slow and row["timing"] not in MEASURED:
            not_evidenced.append({"measure": "wpm", "section": row["section"], "wpm": wpm,
                                  "timing": row["timing"], "why": PAUSES_UNKNOWN,
                                  "command": ""})
            continue
        own = [s for s in shots if str(s.get("section", "")) == row["section"]]
        take0 = next((t for t in takes if str(t.get("section", "")) == row["section"]), {})
        shot = own[0] if own else {"label": take0.get("label", ""), "at": take0.get("at", 0)}
        outside = wpm < fail_lo or wpm > fail_hi
        band = (f"fail band {fail_lo}-{fail_hi}" if outside else
                f"warn band {warn_lo}-{warn_hi}")
        found.append(_finding(
            "wpm", shot, timestamp=float(shot.get("at") or 0), scope="section",
            section=row["section"], severity="blocking" if outside else "minor",
            problem=f"section '{row['section']}' reads at {wpm:.0f} wpm ({band})",
            why=("words over voiced seconds, silences over "
                 f"{value('wpm_gap_s'):g} s left out (direction 25; timing "
                 f"{row['timing']}): " + ("too fast to be followed" if fast else
                                          "slow enough to lose the listener")),
            owner="voice",
            root_cause=("the read runs faster than the words can be followed" if fast else
                        "the read is slowed down where the picture should do the "
                        "explaining"),
            fix=("record the section's takes again slower, or thin its lines" if fast else
                 "record the section's takes again at a natural pace, and let the "
                 "picture carry the clarity")))

    return {
        "shot_lengths": lengths, "duration": duration,
        "cv": {"shots": cv_shots, "events": cv_events, "basis": basis,
               "intervals": len(gaps) if scanned else len(lengths), "floor": floor},
        "events": events, "longest_hold": longest, "holds_under_narration": under,
        "opening": {"seconds": value("opening_s"), "rate": rate, "median": median},
        "edge": sorted(edge), "sections": sections, "scan": scan,
        "fps": fps if scanned else None,
        "thresholds": {k: RHYTHM[k]["value"] for k in RHYTHM if k != "calibrated"},
        "calibrated": calibrated(), "not_evidenced": not_evidenced, "findings": found}


def rhythm_findings(build: dict | None, takes, scores=None, edge: set | None = None, *,
                    fps: float | None = None) -> list[dict]:
    """The findings `measure` files, alone."""
    return measure(build, takes, scores, fps=fps, edge=edge)["findings"]


def brief_of(block: dict) -> dict:
    """What the reviewer is shown of the block: the numbers, labelled as
    measurements, never the series and never in place of its judgement."""
    keep = ("shot_lengths", "cv", "longest_hold", "opening", "sections", "calibrated",
            "not_evidenced")
    return {"about": "measured from build.json, voice.json and the render's scene "
                     "scores, not judged: evidence for editing_pacing beside your own "
                     "judgement, which it does not replace",
            **{k: block.get(k) for k in keep}}


#: The function names a shot can take (script.FUNCTIONS): the edge reads two.
assert {"hook", "payoff"} <= set(script.FUNCTIONS)
