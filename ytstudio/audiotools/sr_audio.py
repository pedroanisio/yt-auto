"""Shared code of the gate's audio tools: sr-margin, sr-audiocheck, sr-sibilance, tail_check.

Standard library and ffmpeg only: the render hosts' python3 may have no numpy.
The tools measure; they decide nothing creative. Every threshold is named below
with where it comes from, and says so when it comes from no standard.

Exit codes of every tool: 0 pass, 1 a finding, 2 could not measure (a missing
engine or ffmpeg, an unreadable scene). The gate records stdout and stderr.
"""

from __future__ import annotations

import argparse
import cmath
import contextlib
import heapq
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET
from array import array
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------------- #

#: The scale's bottom. ITU-R BS.1770 gates blocks below -70 LUFS as silence.
FLOOR_LUFS = -70.0
#: dBFS floor for an RMS of exactly zero (ffmpeg prints -inf).
FLOOR_DB = -120.0

#: STANDARD (pnkit docs, growth-kit-proposal.md: "sr-margin, floor 11 dB"): the
#: narration sits at least this far over everything else, per line.
MARGIN_FLOOR_DB = 11.0
#: STANDARD (EBU Tech 3341 / R128 short-term loudness): a 3 s window.
WINDOW_S = 3.0
#: STANDARD (ITU-R BS.1770-4 relative gate): blocks more than 10 LU under the
#: ungated level are not counted. Here it drops the pauses inside a line.
RELATIVE_GATE_LU = 10.0
#: Fewest voiced 100 ms frames (one second) a window needs to be judged.
MIN_VOICED_FRAMES = 10

#: STANDARD (EBU R128 s1 and ITU-R BS.1770 delivery practice; the gate's own
#: check in production_gate.py): true peak at or under -1 dBTP.
TRUE_PEAK_CEILING_DB = -1.0
#: NO STANDARD FIXES THIS. Working convention, to be calibrated on real films:
#: a DC offset over 0.5% of full scale (-46 dBFS) wastes headroom and clicks.
DC_OFFSET_LIMIT = 0.005
#: NO STANDARD. Working convention: digital silence (at or under -90 dBFS)
#: of 2 s or more inside the programme is a dropout, not a pause. Head and tail
#: are not "inside". A pause with room tone is not digital silence.
SILENCE_MAX_S = 2.0
SILENCE_FLOOR_DB = -90.0
SILENCE_DETECT_S = 0.5
EDGE_S = 0.1
#: STANDARD METHOD, NO STANDARD LIMIT. EBU Tech 3342 computes loudness range
#: as the 10th to 95th percentile of short-term loudness after a -20 LU
#: relative gate; the limit for speech alone is a working convention.
SPEECH_RANGE_LIMIT_LU = 15.0
RANGE_GATE_LU = 20.0
RANGE_LOW_PCT, RANGE_HIGH_PCT = 10, 95
#: NO STANDARD. Working convention after eko/qc's "Muted channel" row: a channel
#: whose RMS over the whole file is at or under -90 dBFS (the digital-silence
#: floor above) while another channel is over -60 dBFS is muted, as from a mono
#: source on one side or a pan or bus error. Integrated loudness drops only
#: about 3 LU, so the loudness window can still pass it. Both channels silent is
#: the silence check's business, not this one's.
MUTED_CHANNEL_DB = -90.0
MUTED_CARRIER_DB = -60.0
#: NO STANDARD. Working convention after eko/qc's "Phase" row (out of phase at
#: <= -0.5 for 1 s), on ffmpeg's aphasemeter correlation (-1 anti-phase, 0
#: uncorrelated, +1 mono): a programme whose mean is under 0, or a run of at
#: least 1 s at or under -0.5, cancels when folded to mono (phone speakers,
#: some TV paths). A fault threshold, not a judgement of the stereo width.
PHASE_MEAN_FLOOR = 0.0
PHASE_RUN_FLOOR = -0.5
PHASE_RUN_S = 1.0
PHASE_KEY = "lavfi.aphasemeter.phase"

#: Sibilant band, Hz. 5-10 kHz is where fricatives carry their energy.
SIBILANCE_BAND = (5000, 10000)
#: Analysis windows of 50 ms; a burst must last two (100 ms) to be a sibilant
#: and not a click.
SIBILANCE_WINDOW_S = 0.05
SIBILANCE_MIN_WINDOWS = 2
#: NO STANDARD. Working convention, to be calibrated on real narration: the
#: band's 50 ms RMS should not rise above the take's overall RMS (de-essers
#: are commonly set to act where the sibilant band exceeds the voice's average).
SIBILANCE_LIMIT_DB = 0.0
SIBILANCE_RATE = 32000

#: tail_check, ported from pnkit's tools/tail_check.py with its constants (calibrated
#: 2026-10-01 on endings heard as laser beams: those held one partial 1.0-1.2 s at a
#: 41-63 % mean share; the redesigned endings measure at most 0.21 s and 7-18 %).
TAIL_RATE = 24000
TAIL_HELD_MAX, TAIL_SHARE_MAX, TAIL_DECAY_DB = 0.4, 0.3, 15.0
TAIL_NPERSEG, TAIL_NOVERLAP = 4096, 3072
#: The ending starts this long before the last word ends (pnkit.audio.TAIL_LEAD).
TAIL_LEAD_S = 0.5
#: NO STANDARD. Rule 1, by measurement: a soft struck effect reaches its peak within
#: this long of starting, and ends this far under it (or fades out). A sweep, a swell
#: or a held tone does neither.
STRUCK_ATTACK_MAX_S = 0.15
STRUCK_DECAY_DB = 15.0
#: the peak is reached at the first frame within this many dB of the loudest
STRUCK_PEAK_TOL_DB = 3.0
#: the sound begins at the first frame this far above the file's own noise floor (the
#: quietest tenth of its frames), or above peak - STRUCK_ONSET_RANGE_DB if that is lower
STRUCK_ONSET_MARGIN_DB = 12.0
STRUCK_ONSET_RANGE_DB = 40.0
STRUCK_FADE_OUT_S = 0.2
STRUCK_FRAME_S = 0.01
STRUCK_END_S = 0.1
EFFECT_ROLES = ("effects", "effect", "sfx")


class ToolError(RuntimeError):
    """The tool could not measure (environment, not a finding)."""


@dataclass
class Frame:
    t: float   # end of the window, seconds
    m: float   # momentary loudness, LUFS (400 ms)
    s: float   # short-term loudness, LUFS (3 s)


@dataclass
class Track:
    id: str
    path: Path
    start: float
    end: float


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #

def ffmpeg_bin() -> str:
    return os.environ.get("SR_FFMPEG") or "ffmpeg"


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-nostdin", "-hide_banner", "-v", "info", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise ToolError(f"cannot run {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise ToolError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()[-300:]}")
    return proc


def _db(text: str, floor: float) -> float:
    try:
        value = float(text)
    except ValueError:
        return floor
    return floor if math.isinf(value) or math.isnan(value) else max(value, floor)


_NUM = r"(-?inf|-?[\d.]+)"
_FRAME = re.compile(rf"\bt:\s*([\d.]+)\s+TARGET:\s*-?\d+\s*LUFS\s+M:\s*{_NUM}\s+S:\s*{_NUM}\s+I:")


def parse_ebur128(text: str) -> list[Frame]:
    return [Frame(float(m[1]), _db(m[2], FLOOR_LUFS), _db(m[3], FLOOR_LUFS))
            for m in _FRAME.finditer(text)]


def parse_ebur128_summary(text: str) -> dict:
    tail = text.rsplit("Summary:", 1)[-1] if "Summary:" in text else ""

    def pick(pattern):
        m = re.search(pattern, tail, re.S)
        return _db(m[1], FLOOR_DB) if m else None

    return {"integrated": pick(rf"Integrated loudness:\s*I:\s*{_NUM}\s*LUFS"),
            "lra": pick(rf"Loudness range:\s*LRA:\s*{_NUM}\s*LU"),
            "true_peak": pick(rf"True peak:\s*Peak:\s*{_NUM}\s*dBFS")}


def parse_dc_offsets(text: str) -> list[float]:
    """DC offset per channel from astats; the Overall row is the mean, not a channel."""
    out, section = [], ""
    for line in text.splitlines():
        body = line.split("]", 1)[-1].strip()
        if body.startswith("Channel:"):
            section = "channel"
        elif body.startswith("Overall"):
            section = "overall"
        elif body.startswith("DC offset:") and section == "channel":
            out.append(float(body.split(":", 1)[1]))
    return out


def parse_channel_rms(text: str) -> list[float]:
    """RMS level (dBFS) per channel from astats, -inf floored at FLOOR_DB; the
    Overall row is the mix of all channels, not a channel."""
    out, section = [], ""
    for line in text.splitlines():
        body = line.split("]", 1)[-1].strip()
        if body.startswith("Channel:"):
            section = "channel"
        elif body.startswith("Overall"):
            section = "overall"
        elif body.startswith("RMS level dB:") and section == "channel":
            out.append(_db(body.split(":", 1)[1].strip(), FLOOR_DB))
    return out


def parse_silences(text: str, duration: float) -> list[tuple[float, float]]:
    out, start = [], None
    for line in text.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            out.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def parse_ametadata(text: str, key: str) -> list[tuple[float, float]]:
    out, t = [], 0.0
    for line in text.splitlines():
        if "pts_time:" in line:
            t = float(line.split("pts_time:")[1].split()[0])
        elif line.startswith(key + "="):
            out.append((t, _db(line.split("=", 1)[1].strip(), FLOOR_DB)))
    return out


def momentary(path: Path) -> list[Frame]:
    """Momentary and short-term loudness of a file, every 100 ms."""
    proc = run_ffmpeg(["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"])
    frames = parse_ebur128(proc.stderr)
    if not frames:
        raise ToolError(f"no loudness frames from ffmpeg for {path}")
    return frames


# --------------------------------------------------------------------------- #
# the scene
# --------------------------------------------------------------------------- #

def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse(xml_text: str):
    """The tree, with its namespaces registered so a rewrite keeps its prefixes."""
    import io
    root = None
    for event, value in ET.iterparse(io.StringIO(xml_text), events=("start-ns", "start")):
        if event == "start-ns":
            ET.register_namespace(*value)
        elif root is None:
            root = value
    return root


def probe_seconds(path: Path) -> float | None:
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        pass
    probe = (os.environ.get("SR_FFPROBE") or "ffprobe")
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True).stdout.strip()
        return float(out)
    except (OSError, ValueError):
        return None


NARRATION_ROLES = ("voiceover", "dialogue")
NARRATION_BUS = "narration"


def _is_narration(track) -> bool:
    return (track.get("mute") != "true"
            and (track.get("role") in NARRATION_ROLES or track.get("bus") == NARRATION_BUS))


def _num(node, name, default):
    try:
        return float(node.get(name)) if node.get(name) not in (None, "") else default
    except ValueError:
        return default


def narration_tracks(xml_text: str, scene_dir: Path) -> list[Track]:
    """Each narration take of the scene with the span it occupies on the timeline."""
    root = _parse(xml_text)
    assets = {n.get("id"): n for n in root.iter() if _local(n.tag) == "audio" and n.get("id")}
    out = []
    for node in root.iter():
        if _local(node.tag) != "audioTrack" or not _is_narration(node):
            continue
        asset = assets.get(node.get("asset"))
        src = asset.get("src") if asset is not None else None
        if not src or "://" in src:
            continue
        path = Path(src) if Path(src).is_absolute() else Path(scene_dir) / src
        length = _num(asset, "duration", None)
        clip_in = _num(node, "clipIn", 0.0)
        clip_out = _num(node, "clipOut", None)
        if clip_out is None:
            length = length if length is not None else probe_seconds(path)
            clip_out = length
        if clip_out is None:
            continue
        speed = _num(node, "speed", 1.0) or 1.0
        plays = 1 + int(_num(node, "loop", 0))
        start = _num(node, "start", 0.0)
        span = max(0.0, clip_out - clip_in) / speed * plays
        out.append(Track(node.get("id") or path.stem, path, start, start + span))
    return out


def isolate(xml_text: str) -> tuple[str, str]:
    """Two copies of the scene: the narration alone, and everything but it.

    Both leave the master unnormalised and unlimited: the gains stay as the
    scene states them, so the difference of the two mixes is the margin.
    Tracks are moved onto a muted bus; for the rest the narration bus itself is
    routed to the muted bus, so whatever ducks under it still senses it.
    """
    outputs = []
    for side in ("voice", "rest"):
        root = _parse(xml_text)
        ids = {n.get("id") for n in root.iter() if n.get("id")}
        null = "yt-muted"
        while null in ids:
            null += "-"
        for mix in [n for n in root.iter() if _local(n.tag) == "audioMix"]:
            ns = mix.tag[:mix.tag.index("}") + 1] if mix.tag.startswith("{") else ""
            ET.SubElement(mix, f"{ns}bus", {"id": null, "mute": "true"})
            tracks = [n for n in mix if _local(n.tag) == "audioTrack"]
            buses = {n.get("id"): n for n in mix if _local(n.tag) == "bus"}
            for node in mix:
                if _local(node.tag) == "master":
                    node.set("normalize", "none")
                    node.set("limiter", "false")
            narration = [t for t in tracks if _is_narration(t)]
            others = [t for t in tracks if not _is_narration(t)]
            if side == "voice":
                for t in others:
                    t.set("bus", null)
                continue
            shared = {t.get("bus") for t in others}
            for t in narration:
                bus = t.get("bus")
                if bus and bus in buses and bus not in shared:
                    buses[bus].set("output", null)
                else:
                    t.set("bus", null)
        outputs.append(ET.tostring(root, encoding="unicode"))
    return outputs[0], outputs[1]


@contextlib.contextmanager
def isolated_copies(scene: Path):
    """The two isolated scenes in a temp directory that mirrors the scene's own by
    symlinks, so relative assets resolve and the project is never written to."""
    scene = Path(scene).resolve()
    text = scene.read_text(encoding="utf-8")
    voice, rest = isolate(text)
    with tempfile.TemporaryDirectory(prefix="sr-audio-") as tmp:
        for entry in scene.parent.iterdir():
            if entry != scene:
                (Path(tmp) / entry.name).symlink_to(entry)
        paths = []
        for name, body in (("voice", voice), ("rest", rest)):
            p = Path(tmp) / f"yt-{name}.scene.xml"
            p.write_text(body, encoding="utf-8")
            paths.append(p)
        yield paths[0], paths[1]


def render_mix(engine: str, scene: Path, out: Path) -> None:
    """The scene's audio-only mix: the engine writes audio alone for a .wav output."""
    cmd = [engine, "encode", str(scene), "--hw", "software", "-o", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise ToolError(f"cannot run the engine {engine}: {exc}") from exc
    if proc.returncode != 0:
        raise ToolError(f"engine encode failed ({proc.returncode}): "
                        f"{(proc.stderr or proc.stdout).strip()[-400:]}")


# --------------------------------------------------------------------------- #
# arithmetic
# --------------------------------------------------------------------------- #

def power_mean(levels: list[float]) -> float:
    return 10 * math.log10(sum(10 ** (v / 10) for v in levels) / len(levels))


def percentile(sorted_values: list[float], pct: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, max(0, math.ceil(pct / 100 * len(sorted_values)) - 1))]


def span_margins(voice: list[Frame], rest: list[Frame], spans) -> list[dict]:
    """Narration over everything else, per span (label, start, end).

    Frames are the 100 ms momentary readings, taken at the middle of their 400 ms
    window. Within a span only the voiced frames count (10 LU under the span's
    ungated voice level). The margin is the voice's energy mean minus the rest's
    over those frames; `worst` is the same over the worst 3 s window."""
    hop = (voice[1].t - voice[0].t) if len(voice) > 1 else 0.1
    n = min(len(voice), len(rest))
    rows = []
    for label, start, end in spans:
        idx = [i for i in range(n) if start <= voice[i].t - 0.2 <= end]
        row = {"label": label, "start": start, "end": end, "margin": None, "worst": None}
        rows.append(row)
        if not idx:
            continue
        gate = power_mean([voice[i].m for i in idx]) - RELATIVE_GATE_LU
        voiced = [i for i in idx if voice[i].m >= gate]

        def margin(frames):
            return (power_mean([voice[i].m for i in frames])
                    - power_mean([max(rest[i].m, FLOOR_LUFS) for i in frames]))

        if len(voiced) < MIN_VOICED_FRAMES:
            continue
        row["margin"] = margin(voiced)
        size = max(1, round(WINDOW_S / hop))
        voiced_set = set(voiced)
        worst = None
        for a in range(0, max(1, len(idx) - size + 1)):
            window = [i for i in idx[a:a + size] if i in voiced_set]
            if len(window) >= MIN_VOICED_FRAMES:
                m = margin(window)
                worst = m if worst is None else min(worst, m)
        row["worst"] = worst if worst is not None else row["margin"]
    return rows


def margin_findings(rows: list[dict], floor: float = MARGIN_FLOOR_DB) -> list[str]:
    out = []
    for r in rows:
        if r["margin"] is None:
            continue
        where = f"{r['label']} ({r['start']:.1f}-{r['end']:.1f} s)"
        if r["margin"] < floor:
            out.append(f"{where}: narration is {r['margin']:.1f} dB over the rest, floor {floor:g} dB")
        elif r["worst"] is not None and r["worst"] < floor:
            out.append(f"{where}: worst 3 s window is {r['worst']:.1f} dB over the rest, "
                       f"floor {floor:g} dB")
    return out


def spread_in_spans(frames: list[Frame], spans) -> float | None:
    """Loudness range (EBU Tech 3342 method) of the short-term readings whose whole
    3 s window lies inside a narration span; None when there are too few."""
    values = [f.s for f in frames for a, b in spans if a + WINDOW_S <= f.t <= b and f.s > FLOOR_LUFS]
    if len(values) < MIN_VOICED_FRAMES:
        return None
    gate = power_mean(values) - RANGE_GATE_LU
    kept = sorted(v for v in values if v >= gate)
    return percentile(kept, RANGE_HIGH_PCT) - percentile(kept, RANGE_LOW_PCT)


def phase_runs(phase) -> list[tuple[float, float]]:
    """(start, end) of each run of aphasemeter readings at or under PHASE_RUN_FLOOR
    lasting PHASE_RUN_S or more. A reading holds until the next one; the last
    holds for the median step between readings."""
    rows = list(phase)
    if not rows:
        return []
    steps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]))
    end_of_file = rows[-1][0] + (steps[len(steps) // 2] if steps else 0.0)
    out, start = [], None
    for t, value in rows:
        if value <= PHASE_RUN_FLOOR:
            if start is None:
                start = t
            continue
        if start is not None and t - start >= PHASE_RUN_S:
            out.append((start, t))
        start = None
    if start is not None and end_of_file - start >= PHASE_RUN_S:
        out.append((start, end_of_file))
    return out


def channel_findings(channels) -> list[str]:
    channels = list(channels)
    if len(channels) < 2:
        return []
    loudest = max(range(len(channels)), key=lambda n: channels[n])
    if channels[loudest] <= MUTED_CARRIER_DB:
        return []
    return [f"channel {n + 1} is silent ({value:.1f} dBFS RMS) while channel {loudest + 1} "
            f"carries the programme"
            for n, value in enumerate(channels) if value <= MUTED_CHANNEL_DB]


def mean_phase(phase) -> float | None:
    values = [v for _, v in phase]
    return statistics.fmean(values) if values else None


def phase_findings(phase) -> list[str]:
    out = []
    mean = mean_phase(phase)
    if mean is not None and mean < PHASE_MEAN_FLOOR:
        out.append(f"stereo phase correlation averages {mean:.2f}; the mix cancels in mono")
    for a, b in phase_runs(phase):
        out.append(f"stereo out of phase (<= {PHASE_RUN_FLOOR:g}) {a:.1f}-{b:.1f} s")
    return out


def audio_findings(*, true_peak, dc, silences, duration, spans, short_term,
                   channels=(), phase=()) -> list[str]:
    out = []
    if true_peak is not None and true_peak > TRUE_PEAK_CEILING_DB:
        out.append(f"true peak {true_peak:.1f} dBTP over the {TRUE_PEAK_CEILING_DB:g} dBTP ceiling")
    for n, value in enumerate(dc, 1):
        if abs(value) > DC_OFFSET_LIMIT:
            out.append(f"DC offset {value:+.4f} on channel {n}, limit {DC_OFFSET_LIMIT:g}")
    for a, b in silences:
        if a <= EDGE_S or b >= duration - EDGE_S or b - a < SILENCE_MAX_S:
            continue
        out.append(f"digital silence {a:.2f}-{b:.2f} s ({b - a:.2f} s) inside the programme, "
                   f"limit {SILENCE_MAX_S:g} s")
    spread = spread_in_spans(short_term, spans) if spans else None
    if spread is not None and spread > SPEECH_RANGE_LIMIT_LU:
        out.append(f"loudness range inside the narration is {spread:.1f} LU, "
                   f"limit {SPEECH_RANGE_LIMIT_LU:g} LU")
    return out + channel_findings(channels) + phase_findings(phase)


def sibilant_bursts(rows, overall_db: float, limit_db: float) -> list[tuple[float, float, float]]:
    """(start, end, peak excess dB) of runs of at least two consecutive windows whose
    band level exceeds the take's overall level by more than the limit."""
    out, run = [], []
    for t, level in list(rows) + [(None, None)]:
        if t is not None and level - overall_db > limit_db:
            run.append((t, level - overall_db))
            continue
        if len(run) >= SIBILANCE_MIN_WINDOWS:
            out.append((run[0][0], run[-1][0] + SIBILANCE_WINDOW_S, max(e for _, e in run)))
        run = []
    return out


# --------------------------------------------------------------------------- #
# file measurements
# --------------------------------------------------------------------------- #

def audiocheck_measure(path: Path) -> dict:
    loud = run_ffmpeg(["-i", str(path), "-vn", "-af", "ebur128=peak=true,astats=metadata=0",
                       "-f", "null", "-"]).stderr
    frames = parse_ebur128(loud)
    summary = parse_ebur128_summary(loud)
    quiet = run_ffmpeg(["-i", str(path), "-vn", "-af",
                        f"silencedetect=n={SILENCE_FLOOR_DB:g}dB:d={SILENCE_DETECT_S:g}",
                        "-f", "null", "-"]).stderr
    duration = frames[-1].t if frames else 0.0
    channels = parse_channel_rms(loud)
    phase = []
    # aphasemeter folds any other layout to stereo without saying so; its number
    # would then describe a downmix, not the file
    if len(channels) == 2:
        phase = parse_ametadata(run_ffmpeg(
            ["-i", str(path), "-vn", "-af", f"aphasemeter=video=0,ametadata=print:key={PHASE_KEY}:file=-",
             "-f", "null", "-"]).stdout, PHASE_KEY)
    return {"true_peak": summary["true_peak"], "integrated": summary["integrated"],
            "dc": parse_dc_offsets(loud), "frames": frames, "duration": duration,
            "silences": parse_silences(quiet, duration), "channels": channels, "phase": phase}


def measured_findings(m: dict, spans) -> list[str]:
    return audio_findings(true_peak=m["true_peak"], dc=m["dc"], silences=m["silences"],
                          duration=m["duration"], spans=spans, short_term=m["frames"],
                          channels=m.get("channels", ()), phase=m.get("phase", ()))


def audiocheck_file(path: Path, spans) -> list[str]:
    return measured_findings(audiocheck_measure(path), spans)


def sibilance_rows(path: Path, band: tuple[int, int] | None) -> list[tuple[float, float]]:
    chain = [f"aresample={SIBILANCE_RATE}"]
    if band:
        chain += [f"highpass=f={band[0]}", f"lowpass=f={band[1]}"]
    key = "lavfi.astats.Overall.RMS_level"
    chain += [f"asetnsamples=n={round(SIBILANCE_RATE * SIBILANCE_WINDOW_S)}:p=0",
              "astats=metadata=1:reset=1", f"ametadata=print:key={key}:file=-"]
    proc = run_ffmpeg(["-i", str(path), "-vn", "-af", ",".join(chain), "-f", "null", "-"])
    return parse_ametadata(proc.stdout, key)


def sibilance_findings(path: Path) -> list[str]:
    full = sibilance_rows(path, None)
    band = sibilance_rows(path, SIBILANCE_BAND)
    if not full or not band:
        raise ToolError(f"no windows measured in {path}")
    overall = power_mean([v for _, v in full])
    gate = overall - RELATIVE_GATE_LU
    voiced = [(t, b) for (t, f), (_, b) in zip(full, band) if f >= gate]
    return [f"{Path(path).name} {a:.2f}-{b:.2f} s: the {SIBILANCE_BAND[0] // 1000}-"
            f"{SIBILANCE_BAND[1] // 1000} kHz band is {e:+.1f} dB over the take's level, "
            f"limit {SIBILANCE_LIMIT_DB:+g} dB"
            for a, b, e in sibilant_bursts(voiced, overall, SIBILANCE_LIMIT_DB)]


AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".opus")


def project_takes(project: Path) -> list[Path]:
    """The narration takes of a project: those its scene places, else voice/*."""
    project = Path(project)
    scenes = [project / "scene.xml"] + sorted(project.glob("*.scene.xml"))
    for scene in scenes:
        if scene.is_file():
            seen, out = set(), []
            for t in narration_tracks(scene.read_text(encoding="utf-8"), scene.parent):
                if t.path not in seen:
                    seen.add(t.path)
                    out.append(t.path)
            return out
    voice = project / "voice"
    return sorted(p for p in voice.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES) \
        if voice.is_dir() else []


# --------------------------------------------------------------------------- #
# the ending: tail_check
# --------------------------------------------------------------------------- #

def decode_pcm(path, rate: int = TAIL_RATE) -> list[float]:
    """The file's audio, mono, at `rate`, as floats (what ffmpeg decodes)."""
    cmd = [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
           "-ar", str(rate), "-f", "f32le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except OSError as exc:
        raise ToolError(f"cannot run {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise ToolError(f"{cmd[0]} failed ({proc.returncode}) on {path}: "
                        f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    samples = array("f")
    samples.frombytes(proc.stdout[:len(proc.stdout) // 4 * 4])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tolist()


_PLANS: dict[int, tuple[list[int], list[complex]]] = {}


def fft(x: list[complex]) -> list[complex]:
    """Discrete Fourier transform of a power-of-two length (iterative radix 2)."""
    n = len(x)
    if n & (n - 1):
        raise ValueError(f"fft length {n} is not a power of two")
    if n not in _PLANS:
        bits = n.bit_length() - 1
        order = [int(format(i, f"0{bits}b")[::-1], 2) if bits else 0 for i in range(n)]
        _PLANS[n] = (order, [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)])
    order, twiddle = _PLANS[n]
    a = [x[i] for i in order]
    size = 2
    while size <= n:
        half, step = size // 2, n // size
        for start in range(0, n, size):
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * twiddle[k * step]
                a[start + k] = u + v
                a[start + k + half] = u - v
        size *= 2
    return a


def frame_powers(frames: list[list[float]], window: list[float], scale: float) -> list[list[float]]:
    """|FFT|^2 of each real frame (windowed, scaled), bins 0..n/2. Two frames share one
    complex transform: one rides the real part, the other the imaginary."""
    n = len(window)
    out = []
    for i in range(0, len(frames), 2):
        a = frames[i]
        b = frames[i + 1] if i + 1 < len(frames) else [0.0] * n
        z = fft([complex(a[j] * window[j], b[j] * window[j]) for j in range(n)])
        pa, pb = [], []
        for k in range(n // 2 + 1):
            zk, zn = z[k], z[-k % n].conjugate()
            xa = (zk + zn) / 2 * scale
            xb = (zk - zn) / 2j * scale
            pa.append(xa.real ** 2 + xa.imag ** 2)
            pb.append(xb.real ** 2 + xb.imag ** 2)
        out.append(pa)
        if i + 1 < len(frames):
            out.append(pb)
    return out


def stft_power(seg: list[float]) -> list[list[float]]:
    """Power columns as scipy.signal.stft(seg, fs, nperseg=4096, noverlap=3072) gives them:
    periodic Hann window, zeros padded half a segment at both ends and to a whole
    number of segments, spectrum scaling (1 / sum of the window)."""
    n, hop = TAIL_NPERSEG, TAIL_NPERSEG - TAIL_NOVERLAP
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]
    padded = [0.0] * (n // 2) + list(seg) + [0.0] * (n // 2)
    padded += [0.0] * ((-(len(padded) - n) % hop) % n)
    count = (len(padded) - n) // hop + 1
    frames = [padded[i * hop:i * hop + n] for i in range(count)]
    return frame_powers(frames, window, 1.0 / sum(window))


def _db10(power: float) -> float:
    return 10 * math.log10(power)


def _rank(values) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    for r, i in enumerate(order):
        ranks[i] = float(r)
    return ranks


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0.0


def _rms_db(a) -> float:
    if not len(a):
        return math.nan
    return 20 * math.log10(math.sqrt(sum(v * v for v in a) / len(a)) + 1e-9)


def tail_measure(x: list[float], voice_end: float) -> dict:
    """The ending's figures, as pnkit's tail_check measures them: tail seconds, longest held
    pure partial (s), its mean share, energy above 1.5 kHz, glide, the last 0.1 s and the
    level 2 s earlier (dBFS)."""
    sr = TAIL_RATE
    seg = x[int(voice_end * sr):]
    if not len(seg):
        raise ToolError(f"the last word ends at {voice_end:g} s, past the end of the audio "
                        f"({len(x) / sr:.2f} s)")
    cols = stft_power(seg)
    nbins = TAIL_NPERSEG // 2 + 1
    freq = [k * sr / TAIL_NPERSEG for k in range(nbins)]
    lo_pure = next(k for k, f in enumerate(freq) if f > 380)
    lo_total = next(k for k, f in enumerate(freq) if f > 120)
    lo_band = next(k for k, f in enumerate(freq) if f > 500)
    lo_high = next((k for k, f in enumerate(freq) if f > 1500), nbins)

    tot = [sum(c) for c in cols]
    active = [t > max(tot) * 0.01 for t in tot]
    fr, pu, lv = [], [], []
    for col in cols:
        total = sum(col[lo_total:]) + 1e-12
        i = max(range(lo_pure, nbins), key=col.__getitem__)
        if col[i] <= 0:
            i = 0
        fr.append(freq[i])
        pu.append(sum(col[max(0, i - 2):i + 3]) / total)
        lv.append(_db10(total))
    loud = [v > max(lv) - 20 for v in lv]
    best = run = 0
    f0 = None
    for a_, p_, l_ in zip(fr, pu, loud):
        if l_ and p_ > 0.35 and (f0 is None or abs(a_ - f0) < 0.03 * f0):
            run += 1
            f0 = a_ if f0 is None else f0
        else:
            run, f0 = (1, a_) if (l_ and p_ > 0.35) else (0, None)
        best = max(best, run)
    hop = TAIL_NPERSEG - TAIL_NOVERLAP
    held = best * hop / sr
    share = sum(p for p, l_ in zip(pu, loud) if l_) / sum(loud)

    dom, ton = [], []
    for col, on in zip(cols, active):
        if not on:
            continue
        band = col[lo_band:]
        dom.append(freq[lo_band + max(range(len(band)), key=band.__getitem__)])
        ton.append(sum(heapq.nlargest(3, band)) / (sum(band) + 1e-12))
    glide = False
    if len(dom) > 12:                                     # any 1.5 s stretch with a steady tonal slide
        w = int(1.5 * sr / hop)
        for i in range(0, len(dom) - w, max(1, w // 3)):
            d = dom[i:i + w]
            if (max(d) > min(d) and statistics.median(ton[i:i + w]) > 0.5
                    and max(d) / max(50.0, min(d)) >= 1.5):
                steps = [b - a for a, b in zip(d, d[1:])]
                smooth = sum(abs(s) < 0.12 * a for s, a in zip(steps, d)) / len(steps)
                moving = sum(s != 0 for s in steps) / len(steps)
                if abs(_pearson(_rank(d), list(range(len(d))))) > 0.8 and smooth > 0.9 and moving > 0.5:
                    glide = True
    everything = sum(tot)
    return {"tail": len(seg) / sr, "held": held, "share": share,
            "high": sum(sum(c[lo_high:]) for c in cols) / max(1e-12, everything), "glide": glide,
            "last": _rms_db(x[-int(0.1 * sr):]), "before": _rms_db(x[-int(2.1 * sr):-int(1.9 * sr)])}


def tail_report(m: dict, late, bad, label: str) -> tuple[bool, str]:
    """(passes, the one line). `late` is [(start, name)] or None when no scene was given;
    `bad` is [(start, name, why)]: the late effects that are not soft struck."""
    ok = (not bad and m["held"] <= TAIL_HELD_MAX and m["share"] <= TAIL_SHARE_MAX and not m["glide"]
          and m["last"] < m["before"] - TAIL_DECAY_DB)
    cues = ("not checked (no --scene)" if late is None
            else ([f"{n}@{t:.1f}" for t, n in late] or "none"))
    refused = ", ".join(f"{n}@{t:.1f} ({why})" for t, n, why in bad)
    line = (f"{label} tail {m['tail']:.1f} s: cues after the last word {cues}"
            f"{' NOT ALLOWED [' + refused + ']' if bad else ''}; longest held pure partial {m['held']:.2f} s, "
            f"mean share {m['share'] * 100:.0f} %; energy above 1.5 kHz {m['high'] * 100:.1f} %; "
            f"glide {'YES' if m['glide'] else 'no'}; "
            f"last 0.1 s {m['last']:.0f} dBFS vs {m['before']:.0f} -> {'PASS' if ok else 'CHECK'}")
    return ok, line


def struck_onset(samples, rate: int, fade_in: float = 0.0, fade_out: float = 0.0) -> tuple[bool, str]:
    """Is this effect a soft struck sound? By measurement: it reaches its peak within
    STRUCK_ATTACK_MAX_S of its onset, not of the file's start (a fade-in counts) and ends STRUCK_DECAY_DB under it, or fades out
    over STRUCK_FADE_OUT_S. (ok, why not)."""
    size = max(1, round(rate * STRUCK_FRAME_S))
    levels = [_rms_db(samples[i:i + size]) for i in range(0, len(samples), size)]
    if not levels:
        return False, "the sound is empty"
    peak = max(range(len(levels)), key=levels.__getitem__)
    first = next(i for i, v in enumerate(levels) if v >= levels[peak] - STRUCK_PEAK_TOL_DB)
    floor = sorted(levels)[len(levels) // 10]
    gate = min(floor + STRUCK_ONSET_MARGIN_DB, levels[peak] - STRUCK_ONSET_RANGE_DB)
    onset = next(i for i, v in enumerate(levels) if v >= gate)
    attack = (first - onset + 1) * STRUCK_FRAME_S + fade_in
    if attack > STRUCK_ATTACK_MAX_S:
        return False, f"attack {attack:.2f} s, over {STRUCK_ATTACK_MAX_S:g} s"
    end = _rms_db(samples[-max(1, round(rate * STRUCK_END_S)):])
    if fade_out < STRUCK_FADE_OUT_S and end > levels[peak] - STRUCK_DECAY_DB:
        return False, (f"does not decay: ends {levels[peak] - end:.0f} dB under its peak, "
                       f"needs {STRUCK_DECAY_DB:g}")
    return True, ""


def _is_effect(track) -> bool:
    return (track.get("mute") != "true"
            and (track.get("role") in EFFECT_ROLES or track.get("bus") in EFFECT_ROLES))


def scene_tail_effects(xml_text: str, scene_dir: Path, voice_end: float,
                       lead: float = TAIL_LEAD_S) -> tuple[list, list]:
    """(late, bad): the scene's effect tracks that start after the last word minus `lead`,
    [(start, id)], and those among them that are not soft struck, [(start, id, why)].
    Each is judged by measuring its own audio over the span the track plays."""
    root = _parse(xml_text)
    assets = {n.get("id"): n for n in root.iter() if _local(n.tag) == "audio" and n.get("id")}
    late, bad = [], []
    for node in root.iter():
        if _local(node.tag) != "audioTrack" or not _is_effect(node):
            continue
        start = _num(node, "start", 0.0)
        if start <= voice_end - lead:
            continue
        name = node.get("id") or node.get("asset") or "?"
        late.append((start, name))
        asset = assets.get(node.get("asset"))
        src = asset.get("src") if asset is not None else None
        if int(_num(node, "loop", 0)) > 0:
            bad.append((start, name, "loops, so it is held"))
            continue
        if not src or "://" in src:
            bad.append((start, name, "no audio file to measure"))
            continue
        path = Path(src) if Path(src).is_absolute() else Path(scene_dir) / src
        try:
            samples = decode_pcm(path, TAIL_RATE)
        except ToolError as exc:
            bad.append((start, name, f"cannot be measured: {exc}"))
            continue
        a = int(_num(node, "clipIn", 0.0) * TAIL_RATE)
        clip_out = _num(node, "clipOut", None)
        samples = samples[a:int(clip_out * TAIL_RATE)] if clip_out is not None else samples[a:]
        ok, why = struck_onset(samples, TAIL_RATE, _num(node, "fadeIn", 0.0), _num(node, "fadeOut", 0.0))
        if not ok:
            bad.append((start, name, why))
    return late, bad


# --------------------------------------------------------------------------- #
# the tools
# --------------------------------------------------------------------------- #

def _finish(tool: str, findings: list[str], detail: str) -> int:
    for line in findings:
        print(f"FAIL {tool}: {line}")
    print(f"{tool}: {'FAIL' if findings else 'PASS'}, {len(findings)} finding(s); {detail}")
    return 1 if findings else 0


def _guard(tool: str, body):
    try:
        return body()
    except ToolError as exc:
        print(f"error: {tool}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ET.ParseError) as exc:
        print(f"error: {tool}: {exc}", file=sys.stderr)
        return 2


def margin_main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sr-margin", description="Narration margin over the rest of the mix, per line.")
    p.add_argument("project")
    p.add_argument("--scene", required=True)
    p.add_argument("--floor", type=float, default=MARGIN_FLOOR_DB, help="dB (default %(default)s)")
    args = p.parse_args(argv)

    def body():
        scene = Path(args.scene)
        tracks = narration_tracks(scene.read_text(encoding="utf-8"), scene.parent)
        if not tracks:
            print("sr-margin: PASS, no narration tracks in the scene; nothing to measure")
            return 0
        engine = os.environ.get("SR")
        if not engine:
            raise ToolError("the engine is not named: set SR to the scene-render binary")
        with isolated_copies(scene) as (voice, rest), tempfile.TemporaryDirectory(prefix="sr-mix-") as tmp:
            outs = []
            for name, copy in (("voice", voice), ("rest", rest)):
                wav = Path(tmp) / f"{name}.wav"
                render_mix(engine, copy, wav)
                outs.append(momentary(wav))
        rows = span_margins(outs[0], outs[1], [(t.id, t.start, t.end) for t in tracks])
        for r in rows:
            if r["margin"] is None:
                print(f"sr-margin: {r['label']} {r['start']:.1f}-{r['end']:.1f} s: no voiced audio measured")
            else:
                print(f"sr-margin: {r['label']} {r['start']:.1f}-{r['end']:.1f} s: margin "
                      f"{r['margin']:.1f} dB, worst 3 s {r['worst']:.1f} dB")
        return _finish("sr-margin", margin_findings(rows, args.floor), f"{len(rows)} line(s), floor {args.floor:g} dB")

    return _guard("sr-margin", body)


def audiocheck_main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sr-audiocheck", description="Peak, silence, DC offset, loudness swing, muted channels and stereo phase of a delivered file.")
    p.add_argument("project")
    p.add_argument("video")
    p.add_argument("--scene")
    args = p.parse_args(argv)

    def body():
        spans = []
        if args.scene and Path(args.scene).is_file():
            scene = Path(args.scene)
            spans = [(t.start, t.end) for t in narration_tracks(scene.read_text(encoding="utf-8"), scene.parent)]
        m = audiocheck_measure(Path(args.video))
        rms = ", ".join(f"{v:.1f}" for v in m["channels"]) or "none"
        mean = mean_phase(m["phase"])
        phase = f"{mean:.2f}" if mean is not None else "n/a (not stereo)"
        print(f"sr-audiocheck: integrated {m['integrated']} LUFS, true peak {m['true_peak']} dBTP, "
              f"DC {m['dc']}, channel RMS {rms} dBFS, mean phase {phase}, "
              f"{len(m['silences'])} silence(s) over {SILENCE_DETECT_S:g} s, {len(spans)} narration span(s)")
        found = measured_findings(m, spans)
        return _finish("sr-audiocheck", found, f"{m['duration']:.1f} s decoded")

    return _guard("sr-audiocheck", body)


def sibilance_main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sr-sibilance", description="Sibilant bursts (5-10 kHz) in each narration take.")
    p.add_argument("project")
    args = p.parse_args(argv)

    def body():
        takes = project_takes(Path(args.project))
        if not takes:
            print("sr-sibilance: PASS, no narration takes; nothing to measure")
            return 0
        found = []
        for take in takes:
            if not take.is_file():
                found.append(f"{take.name}: the narration take is not on disk")
                continue
            got = sibilance_findings(take)
            print(f"sr-sibilance: {take.name}: {len(got)} burst(s)")
            found += got
        return _finish("sr-sibilance", found, f"{len(takes)} take(s)")

    return _guard("sr-sibilance", body)


def tail_check_main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tail_check", description="A film's ending, as it sounds in the encoded file: no held tone, "
                                "no glide, a decay, and only soft struck effects after the last word.")
    p.add_argument("media", help="the encoded film (anything ffmpeg reads)")
    p.add_argument("--voice-end", type=float, help="when the last word ends, film seconds")
    p.add_argument("--timings", help="a narration timings JSON with a 'duration'; the last word ends at --lead plus it")
    p.add_argument("--lead", type=float, default=0.6, help="where the narration starts in the film (with --timings; default 0.6)")
    p.add_argument("--scene", help="the scene document, to check the effects that start in the ending")
    p.add_argument("--out", help="write the line here too")
    p.add_argument("--label", help="what to call the film in the line (default: the media file's name)")
    args = p.parse_args(argv)
    if args.voice_end is None and not args.timings:
        p.error("give --voice-end or --timings")

    def body():
        if args.voice_end is not None:
            voice_end = args.voice_end
        else:
            with open(args.timings, encoding="utf-8") as fh:
                voice_end = args.lead + float(json.load(fh)["duration"])
        late = bad = None
        if args.scene:
            scene = Path(args.scene)
            late, bad = scene_tail_effects(scene.read_text(encoding="utf-8"), scene.parent, voice_end)
        ok, line = tail_report(tail_measure(decode_pcm(args.media), voice_end), late, bad or [],
                               args.label or os.path.basename(args.media))
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(line + "\n")
        print(line)
        return 0 if ok else 1

    return _guard("tail_check", body)
