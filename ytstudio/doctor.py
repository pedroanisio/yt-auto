"""Check the health of the toolchain and of every video, and say what to do next.

Nothing here calls a model or the network. It reads what the stages wrote and
measures it: whether the environment can actually run each stage, how far each
video got, whether what was produced hangs together, and which command moves
it forward.

    yt doctor                       # the environment and every video
    yt doctor --title monty         # one video
    yt doctor --env                 # the toolchain only
    yt doctor --json health.json    # the same, as data

Findings carry a level -- `fail` blocks publishing, `warn` needs a look,
`note` is information -- and, where there is one, the command that fixes them.
The exit code is 1 when anything fails, so it works as a gate in a script.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import env, keyframe, render, workspace
from .assets import licence_tier
from .core import read_json, timecode, write_json

#: Levels, in the order the report sorts them.
LEVELS = ("fail", "warn", "note", "ok")
MARK = {"fail": "x", "warn": "!", "note": "·", "ok": "+"}

#: Narration engines that prove the timeline but must not be published.
SCRATCH_ENGINES = ("stub", "espeak")

#: Captions over this many characters per second fail the delivery gate.
CPS_LIMIT = 17.0

#: Narration drift, as a fraction of the planned runtime, beyond which stages
#: planned from the screenplay (cue sheet, animation hours) no longer fit the
#: recording. Below about 5% a cue sheet usually survives; above it, it does not.
DRIFT_LIMIT = 0.05

#: A downstream artifact older than its input is stale: the later stage was
#: run before the earlier one changed, so its output reflects an older cut.
STALE_PAIRS = (("script", "voice", "yt voice"), ("script", "build", "yt build"),
               ("voice", "build", "yt build"), ("sound", "build", "yt build"),
               ("build", "video", "yt render"))


@dataclass
class Finding:
    level: str
    area: str
    message: str
    fix: str = ""


@dataclass
class Report:
    name: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)

    def add(self, level: str, area: str, message: str, fix: str = "") -> None:
        self.findings.append(Finding(level, area, message, fix))

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)

    @property
    def status(self) -> str:
        if self.count("fail"):
            return "blocked"
        if self.count("warn"):
            return "needs attention"
        return "healthy"


# --------------------------------------------------------------------------- #
# the toolchain
# --------------------------------------------------------------------------- #

def check_environment(work: Path) -> Report:
    report = Report("environment")

    claude = shutil.which("claude")
    if claude:
        report.add("ok", "claude", f"claude CLI at {claude}")
    else:
        report.add("fail", "claude", "the `claude` CLI is not on PATH; no stage "
                   "that writes can run", "install Claude Code and run `claude auth`")

    try:
        from .scene_sdk import sdk
        sdk()
        report.add("ok", "scene sdk", "sr_core.scene importable: build, pilot and "
                   "review can run")
    except Exception as exc:  # the sdk raises its own RuntimeError with a hint
        report.add("warn", "scene sdk", f"sr-core not found ({str(exc)[:80]})",
                   "pip install sr-core, or keep the checkout beside this one")

    binary = render.engine()
    hosts = render.hosts()
    if hosts:
        # The renderers are other machines; nothing is built here. One short
        # ssh per host asks for the engine, the memory and the GPU, and the
        # routing rule (docs/compute.md: memory, not GPU) says what each gets.
        check_hosts(report, hosts)
    elif binary:
        report.add("ok", "engine", f"scene-render at {binary}")
    else:
        report.add("warn", "engine", "no scene-render binary and no RS_RENDER_HOSTS: "
                   "yt render cannot run",
                   "set RS_RENDER_HOSTS=user@host,user@host2, or RS_RENDER_BIN")

    try:
        from .gate import gate_script
        gate = gate_script()
    except Exception:
        gate = None
    report.add("ok" if gate else "note", "gate",
               f"production gate at {gate}" if gate
               else "production_gate.py not found; yt gate cannot run")

    try:
        from .voice import ENGINES, pick_engine
        usable = [n for n, e in ENGINES.items() if e.available()[0]]
        chosen = pick_engine("auto")
        real = [n for n in usable if n not in SCRATCH_ENGINES]
        level = "ok" if real else "warn"
        report.add(level, "voice", f"engines usable: {', '.join(usable) or 'none'}; "
                   f"auto picks {chosen}",
                   "" if real else "pip install kokoro, or set ELEVENLABS_API_KEY, "
                                   "for a publishable narration")
    except Exception as exc:
        report.add("warn", "voice", f"could not probe engines: {exc}")

    ffmpeg = shutil.which("ffmpeg")
    report.add("ok" if ffmpeg else "note", "ffmpeg",
               f"ffmpeg at {ffmpeg}" if ffmpeg
               else "no ffmpeg: resampling and non-PCM stitching are unavailable")

    for name, unlocks in env.KNOWN.items():
        # Names only, never values. A service is unlocked by any variable
        # sharing its prefix: Freesound takes CLIENT_ID/SECRET as well as a key.
        prefix = name.split("_")[0] + "_"
        present = sorted(k for k in os.environ if k == name or k.startswith(prefix))
        if present:
            report.add("ok", "keys", f"{', '.join(present)} set ({unlocks})")
        else:
            report.add("note", "keys", f"{name} not set ({unlocks})")

    ideas = read_json(work / workspace.IDEAS_FILE, warn=False, shape=list)
    store = read_json(work / workspace.STORE_FILE, warn=False, shape=(list, dict))
    if isinstance(store, dict):
        store = store.get("ideas", [])
    report.metrics["ideas"] = len(ideas or [])
    report.metrics["store"] = len(store or [])
    report.add("note", "ideas", f"{len(ideas or [])} idea(s) in {workspace.IDEAS_FILE}, "
               f"{len(store or [])} in the store")

    cooldown = read_json(work / ".host-cooldown.json", warn=False) or {}
    import time
    waiting = {h: e["until"] - time.time() for h, e in cooldown.items()
               if isinstance(e, dict) and e.get("until", 0) > time.time()}
    for host, seconds in waiting.items():
        report.add("note", "downloads", f"{host} is cooling down for another "
                   f"{int(seconds // 60)}m; deferred downloads wait until then")

    for name in (work / "house.md",):
        if name.is_file():
            report.add("ok", "house", f"channel standards at {name}")
    return report


# --------------------------------------------------------------------------- #
# one video
# --------------------------------------------------------------------------- #

def check_hosts(report: Report, hosts: list) -> None:
    """One finding per render host, and the routing each usable one gets. A host
    that takes a TCP connection on the ssh port but does not answer ssh is a
    hung or half-started sshd, not an unplugged machine: it is named as such."""
    usable = [h for h in hosts if h.usable]
    big = render.pick_host(usable, "encode")
    small = render.pick_host(usable, "still")
    for h in hosts:
        if h.usable:
            roles = []
            if h is big:
                roles.append("encode, audio")
            if h is small:
                roles.append("stills, frames, validate")
            report.add("ok", "engine", f"{h.version} on {h.addr} ({h.ram_gb} GB, "
                       f"{h.cores} cores, {h.gpu or 'no gpu'}) -> {'; '.join(roles) or 'slices'}")
        elif h.error and render.tcp_open(h.addr):
            report.add("warn", "engine", f"{h.addr}: accepts TCP on the ssh port but not SSH "
                       f"({_clip(h.error, 80)}); jobs sent there would hang",
                       "restart sshd on that host, or take it out of RS_RENDER_HOSTS")
        else:
            report.add("warn", "engine", f"{h.addr}: {h.error or 'no scene-render binary'}",
                       "check ssh access, or unpack a release under ~/render on that host")
    if not usable:
        report.add("fail", "engine", "no render host answers: yt render cannot run",
                   "fix RS_RENDER_HOSTS or ssh access")


def _clip(text: str, limit: int = 160) -> str:
    """Cut long advice at a word, never mid-word, and mark the cut."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cmd(stage: str, project: workspace.Project, *extra: str) -> str:
    return " ".join(["yt", stage, "--project", str(project.root), *extra])


#: The verdict `yt qa` writes into qa.json when the bar is met (qa.READY;
#: kept here so doctor stays model-free, and held equal by a test).
READY_TO_PUBLISH = "READY TO PUBLISH"

#: Engine finding severities that `--strict` refuses.
STRICT_SEVERITIES = ("warning", "error")


def programme_seconds(project: workspace.Project, build: dict | None) -> float:
    """The programme's length: build.json's record, else scene.xml's project."""
    build = build or read_json(project.path("build"), warn=False) or {}
    seconds = float((build.get("scene") or {}).get("duration") or 0)
    if not seconds and project.has("scene"):
        import re
        head = project.path("scene").read_text(encoding="utf-8", errors="replace")[:4000]
        m = re.search(r"<project\b[^>]*\bduration=\"([\d.]+)\"", head)
        seconds = float(m.group(1)) if m else 0.0
    return seconds


def delivery_evidence(project: workspace.Project, build: dict | None
                      ) -> list[tuple[str, str]]:
    """What stands between the rendered video and publication, as
    (problem, fix) pairs; empty means the evidence is all there.

    build.json's `publishable` only says the build has no placeholder and no
    blocking editorial note. Delivery is proven by files: a final render of
    the whole programme with nothing the engine warned about (what
    `--strict` guarantees, read from the report rather than from a flag), a
    technical gate that found it deliverable and a QA pass at the bar, both
    judged after that render.
    """
    missing: list[tuple[str, str]] = []
    final = _cmd("render", project, "--quality", "final", "--strict")
    rep = read_json(project.source("render"), warn=False) or {}
    outputs = (rep.get("result") or {}).get("outputs") or []
    if not rep.get("ok") or not outputs:
        missing.append(("the last render did not finish cleanly, so there is no "
                        "delivered video", final))
    else:
        tiers = sorted({str(o.get("quality") or "unknown") for o in outputs})
        if tiers != ["final"]:
            missing.append((f"the video was rendered at {', '.join(tiers)} quality; "
                            "delivery is a final render", final))
        said = 0
        for o in outputs:
            strict = [f for f in o.get("findings") or []
                      if str(f.get("severity", "")).lower() in STRICT_SEVERITIES]
            known = [str(f.get("message") or "") for f in strict]
            said += len(strict) + len(o.get("evaluation_warnings") or []) \
                + len(o.get("unsupported") or []) \
                + sum(1 for w in o.get("warnings") or []
                      if not any(m and m in str(w) for m in known))
        if said:
            missing.append((f"the engine reported {said} warning(s) or unsupported "
                            "construct(s) on the render; --strict would have refused it",
                            final))
        duration = programme_seconds(project, build)
        for o in outputs:
            start, end = (list(o.get("range") or [0, 0]) + [0, 0])[:2]
            if duration and (float(start) > 0.05 or float(end) < duration - 0.05):
                missing.append((f"the render covers {float(start):g}-{float(end):g}s of a "
                                f"{duration:g}s programme", final))
                break

    built = build or read_json(project.path("build"), warn=False) or {}
    if built and not int(built.get("audio_tracks") or 0):
        missing.append(("the programme has no audio: no narration and no placed cue",
                        _cmd("sound", project, "--fetch", "--synth") + ", then "
                        + _cmd("build", project)))

    video = project.path("video")
    rendered_at = _mtime(video) if video.exists() else 0.0
    gate_file = project.root / "gate.json"
    gate = read_json(gate_file, warn=False) if gate_file.exists() else None
    if not gate:
        missing.append(("no delivery gate has run on this render", _cmd("gate", project)))
    elif gate.get("mode") == "frames":
        missing.append(("the last gate checked decoded frames only; delivery needs "
                        "the technical gate", _cmd("gate", project)))
    elif _mtime(gate_file) < rendered_at:
        missing.append(("the gate predates the render it should judge", _cmd("gate", project)))
    elif gate.get("exit") != 0 or (gate.get("triage") or {}).get("verdict") != "deliverable":
        blocking = len((gate.get("triage") or {}).get("blocking") or [])
        missing.append((f"the gate found it not deliverable ({blocking} blocking)",
                        _cmd("gate", project)))

    qa_file = project.root / "qa.json"
    qa = read_json(qa_file, warn=False) if qa_file.exists() else None
    if not qa:
        missing.append(("no QA pass has scored this render", _cmd("qa", project)))
    elif _mtime(qa_file) < rendered_at:
        missing.append((f"QA {qa.get('pass', '')} predates the render it should judge",
                        _cmd("qa", project)))
    elif qa.get("verdict") != READY_TO_PUBLISH:
        missing.append((f"QA {qa.get('pass', '')} says {qa.get('verdict')} at "
                        f"{qa.get('overall', '?')}", _cmd("qa", project)))
    return missing


def check_project(project: workspace.Project) -> Report:
    report = Report(project.root.name)
    done = project.done()
    report.metrics["stages_done"] = done
    idea = project.load_idea()
    if idea is None:
        report.add("warn", "idea", "no idea.json or dossier; the directory is not "
                   "a usable project")
        return report
    report.metrics["title"] = idea.title

    # -- research -----------------------------------------------------------
    dossier = read_json(project.source("dossier"), warn=False) if "research" in done else None
    if dossier:
        claims = dossier.get("claims", [])
        low = [c for c in claims if c.get("confidence") == "low"]
        unsourced = [c for c in claims if c.get("source_kind") == "unsourced"]
        gaps = dossier.get("gaps", [])
        # A gap or a flag carries `resolved` once its fix was applied -- the
        # finding stays as provenance, but it no longer blocks anything.
        blockers = [g for g in gaps if g.get("severity") == "blocker"
                    and not g.get("resolved")]
        allow_sa = bool(dossier.get("allow_sa"))
        assets = dossier.get("assets", [])
        usable = [a for a in assets
                  if licence_tier(a.get("licence_verified") or a.get("licence", ""),
                                  allow_sa=allow_sa) is not None
                  or (a.get("commercial_ok") and not a.get("licence_verified"))]
        share_alike = [a for a in assets if a.get("share_alike")]
        report.metrics["research"] = {
            "claims": len(claims), "low_confidence": len(low),
            "unsourced": len(unsourced), "sources": len(dossier.get("sources", [])),
            "assets": len(assets), "assets_usable": len(usable),
            "gaps": len(gaps), "blockers": len(blockers),
            "cost_usd": round((dossier.get("usage") or {}).get("cost_usd", 0), 2)}
        report.add("ok", "research", f"{len(claims)} claims · "
                   f"{len(dossier.get('sources', []))} sources · {len(usable)}/"
                   f"{len(assets)} assets usable")
        if dossier.get("verdict"):
            report.add("note", "research", f"verdict: {_clip(dossier['verdict'])}")
        # Provenance: a research pass whose tool calls were denied answers
        # from memory with no error anywhere, so the absence of opened pages
        # is the only trace it leaves.
        opened = dossier.get("urls_opened", [])
        if claims and not opened:
            report.add("fail", "research", "claims exist but no page was opened "
                       "during research; the dossier may be written from memory",
                       _cmd("research", project, "--fresh"))
        for warning in dossier.get("warnings", []):
            report.add("warn", "research", warning, _cmd("research", project, "--fresh"))
        if opened and not dossier.get("warnings"):
            blind = [str(n) for n, e in enumerate(dossier.get("notes", []), 1)
                     if not e.get("visited")]
            if blind:
                report.add("warn", "research", f"question(s) {', '.join(blind)} "
                           "opened no page; those facts rest on search snippets")
        for gap in blockers:
            report.add("fail", "research", f"blocker: {_clip(gap.get('issue', ''))}",
                       _clip(gap.get("how_to_resolve", "")))
        if unsourced:
            report.add("warn", "research", f"{len(unsourced)} claim(s) have no source",
                       "cut them from the script or find a source")
        if claims and len(low) > len(claims) / 4:
            report.add("warn", "research", f"{len(low)} of {len(claims)} claims are "
                       "low confidence", _cmd("research", project, "--fresh",
                                              "--depth", "8"))
        if share_alike:
            report.add("warn", "assets", f"{len(share_alike)} asset(s) are "
                       "ShareAlike; the licence reaches the cut",
                       "swap for public-domain equivalents, or accept the terms")
        if assets and not usable:
            report.add("warn", "assets", "no usable asset was found",
                       _cmd("research", project, "--agentic-assets"))

        manifest = read_json(project.root / "raw" / "08-downloads.json",
                             warn=False, shape=list) or []
        if manifest:
            by = {}
            for row in manifest:
                key = str(row.get("status", "")).split(":")[0]
                by[key] = by.get(key, 0) + 1
            report.metrics["downloads"] = by
            report.add("note", "downloads", ", ".join(f"{v} {k}" for k, v in by.items()))
            if by.get("deferred"):
                report.add("warn", "downloads", f"{by['deferred']} download(s) "
                           "deferred by a rate-limiting host",
                           _cmd("research", project, "--download") + "  (after the cooldown)")
            if by.get("failed"):
                report.add("warn", "downloads", f"{by['failed']} download(s) failed")

    # -- styles ------------------------------------------------------------
    styles = read_json(project.source("styles"), warn=False) if "styles" in done else None
    if styles:
        ranked = styles.get("styles", [])
        top = ranked[0] if ranked else {}
        report.metrics["styles"] = {"count": len(ranked),
                                    "top": top.get("name", ""),
                                    "fit": top.get("fit")}
        report.add("ok", "styles", f"{len(ranked)} treatment(s); primary "
                   f"{top.get('name', '?')} at {top.get('fit', '?')}/10")
        try:
            if ranked and float(top.get("fit", 0)) < 6:
                report.add("warn", "styles", "no treatment scored 6 or better; "
                           "the message may not have a visual carrier",
                           _cmd("styles", project, "--rich", "--deliberate", "full"))
        except (TypeError, ValueError):
            pass

    # -- script ------------------------------------------------------------
    script = read_json(project.source("script"), warn=False) if "script" in done else None
    shots: list[dict] = []
    if script:
        sections = script.get("sections", [])
        shots = [s for sec in sections for s in sec.get("shots", [])]
        words = sum(len(str(s.get("narration", "")).split()) for s in shots)
        planned = sum(int(s.get("seconds", 0) or 0) for s in shots)
        target = int(script.get("target_seconds", 0) or 0)
        unsourced = [s for s in shots
                     if str(s.get("source", "")).strip().upper() == "UNSOURCED"]
        check = script.get("check") or {}
        flags = [f for f in check.get("flags", []) if not f.get("resolved")]
        resolved = [f for f in check.get("flags", []) if f.get("resolved")]
        blockers = [f for f in flags if f.get("severity") == "blocker"]
        important = [f for f in flags if f.get("severity") == "important"]
        if resolved:
            # Whether the edited lines were re-recorded is the stale-take
            # check's job, below; this only records that the flags were acted on.
            report.add("note", "fact-check", f"{len(resolved)} flag(s) resolved and "
                       "annotated in script.md")
        report.metrics["script"] = {
            "sections": len(sections), "shots": len(shots), "words": words,
            "planned_seconds": planned, "target_seconds": target,
            "unsourced_shots": len(unsourced), "fact_check_flags": len(flags),
            "failed_sections": script.get("failed_sections", 0),
            "polished": script.get("polished", 0),
            "retimed": "retimed_seconds" in script}
        report.add("ok", "script", f"{len(sections)} sections · {len(shots)} shots · "
                   f"{words} words · {timecode(planned)} planned"
                   + (f" of {timecode(target)} target" if target else ""))
        if script.get("failed_sections"):
            report.add("fail", "script", f"{script['failed_sections']} section(s) "
                       "failed to write and are empty", _cmd("script", project))
        retimed = bool(script.get("retimed_seconds"))
        if target and planned and abs(planned - target) > target * 0.15:
            if retimed:
                # The durations are measured from the recording now; the
                # target was the brief. Worth knowing, not worth a rewrite.
                report.add("note", "script", f"recorded length {timecode(planned)} "
                           f"against a {timecode(target)} brief; the measured "
                           "timings are in the screenplay")
            else:
                report.add("warn", "script", f"planned runtime {timecode(planned)} is "
                           f"{'over' if planned > target else 'under'} the "
                           f"{timecode(target)} target by more than 15%",
                           _cmd("script", project, "--minutes", f"{target / 60:g}"))
        for flag in blockers:
            where = f"shot {flag['n']}" if flag.get("n") else "script-wide"
            report.add("fail", "fact-check", f"{where}: {_clip(flag.get('problem', ''))}",
                       _clip(flag.get("fix", "")))
        if important:
            report.add("warn", "fact-check", f"{len(important)} important flag(s) "
                       "from --verify", "read the Fact-check section of script.md")
        if unsourced:
            report.add("warn", "script", f"{len(unsourced)} shot(s) are marked "
                       "UNSOURCED", "cut the line or source it")
        if dossier and not flags and not check:
            report.add("note", "script", "written without --verify; no fact-check "
                       "against the research", _cmd("script", project, "--verify"))

    # -- voice -------------------------------------------------------------
    voice = read_json(project.source("voice"), warn=False) if "voice" in done else None
    if voice:
        engine = voice.get("engine", "?")
        drift = float(voice.get("drift_seconds", 0) or 0)
        planned = float(voice.get("planned_seconds", 0) or 0)
        hot = voice.get("too_fast_for_captions", [])
        overlaps = voice.get("overlaps", [])
        timing = voice.get("timing", [])
        report.metrics["voice"] = {
            "engine": engine, "takes": len(voice.get("takes", [])),
            "spoken_seconds": voice.get("spoken_seconds"), "drift_seconds": drift,
            "too_fast": len(hot), "overlaps": len(overlaps), "timing": timing}
        if voice.get("wordless"):
            cues = (voice.get("descriptive_captions") or {}).get("cues", 0)
            report.add("ok", "voice", f"a wordless film with {cues} descriptive "
                       "captions")
        else:
            report.add("ok", "voice", f"{engine} · {len(voice.get('takes', []))} takes · "
                       f"spoken {timecode(voice.get('spoken_seconds', 0) or 0)} · "
                       f"drift {drift:+.0f}s")
        if engine in SCRATCH_ENGINES:
            report.add("warn", "voice", f"narration is a {engine} scratch take, "
                       "not publishable", _cmd("voice", project, "--engine", "auto"))
        if planned and abs(drift) > planned * DRIFT_LIMIT and not (script or {}).get("retimed_seconds"):
            report.add("warn", "voice", f"the read drifts {drift:+.0f}s "
                       f"({abs(drift) / planned:.0%}) from the screenplay; stages "
                       "planned from the script are planning against a runtime "
                       "that does not exist", _cmd("retime", project))
        if hot:
            report.add("warn", "captions", f"{len(hot)} take(s) over {CPS_LIMIT:.0f} "
                       "characters/second; the gate rejects them",
                       "shorten those lines or let them breathe")
        if overlaps:
            report.add("warn", "voice", f"{len(overlaps)} take(s) overlap the next "
                       "on the planned layout", _cmd("voice", project, "--layout",
                                                     "sequential"))
        # A line edited after it was recorded leaves the audio saying one
        # thing and the captions another, with nothing to flag it: the take
        # keeps the text it was read from, so the two can be compared.
        current = {str(s.get("label")): " ".join(str(s.get("narration", "")).split())
                   for s in shots}
        diverged = [t["label"] for t in voice.get("takes", [])
                    if t.get("label") in current
                    and " ".join(str(t.get("text", "")).split()) != current[t["label"]]]
        if diverged:
            shown = ", ".join(diverged[:8]) + (" …" if len(diverged) > 8 else "")
            report.add("warn", "voice", f"{len(diverged)} recorded take(s) no longer "
                       f"match the screenplay line ({shown}); audio and captions "
                       "would disagree", _cmd("voice", project, "--redo",
                                              ",".join(diverged[:20])))
        if "estimated" in timing:
            report.add("note", "voice", "word timings are estimated, not aligned; "
                       "per-shot captions are safe, word-level ones are not")

    # -- vfx ---------------------------------------------------------------
    vfx = read_json(project.source("vfx"), warn=False) if "vfx" in done else None
    if vfx:
        totals = vfx.get("totals", {})
        plan = vfx.get("plan", {})
        report.metrics["vfx"] = {"effects": totals.get("count"),
                                 "hours": totals.get("hours"),
                                 "animated_seconds": totals.get("seconds"),
                                 "shared_rigs": len(totals.get("templates", {}))}
        report.add("ok", "vfx", f"{totals.get('count', 0)} effects · "
                   f"{totals.get('hours', 0):.0f} animator-hours · "
                   f"{len(totals.get('templates', {}))} shared rig(s)")
        if plan.get("verdict"):
            level = "warn" if "not buildable" in plan["verdict"].lower() else "note"
            report.add(level, "vfx", f"plan: {_clip(plan['verdict'])}")
        # Sections are reviewed alone; this is the review of them together.
        cont = vfx.get("continuity") or {}
        breaks = [b for b in cont.get("breaks") or [] if isinstance(b, dict)]
        serious = [b for b in breaks if b.get("severity") in ("blocking", "major")]
        if cont.get("one_film") is False or serious:
            shown = "; ".join(f"{b.get('owner_section', '?')}: {b.get('problem', '')}"
                              for b in (serious or breaks)[:3])
            report.add("warn", "vfx", f"the sections do not read as one film "
                       f"({cont.get('score', '?')}/10): {_clip(shown, 240)}",
                       "re-author the section(s) named; vfx.json `continuity` has each fix")

    # -- keyframe ----------------------------------------------------------
    # The look is locked by the operator, not by the stage: a frame awaiting
    # approval is information, a rejected or changed one needs a look, and
    # an animation authored without an approved look inherited nothing.
    look = keyframe.approval_state(project.root)
    if look["state"] != "none":
        score = look.get("score")
        shown = f"{score:g}/10" if isinstance(score, (int, float)) else "unscored"
        report.metrics["keyframe"] = {"state": look["state"], "score": score, "at": look["at"]}
        if look["state"] == "pending":
            report.add("note", "keyframe", f"the keyframe ({shown}) awaits approval: "
                       f"look at {look['frame'] or 'keyframe/keyframe.png'}, then "
                       f"{_cmd('keyframe', project, '--approve')}",
                       _cmd("keyframe", project, "--approve"))
        elif look["state"] == "rejected":
            report.add("warn", "keyframe", f"the keyframe was rejected: {_clip(look['notes'])}",
                       _cmd("keyframe", project))
        elif look["state"] == "stale":
            report.add("warn", "keyframe", "keyframe.scene.xml changed after it was "
                       "approved; the look on disk is not the approved one",
                       _cmd("keyframe", project, "--approve"))
        else:
            report.add("ok", "keyframe", f"approved {look['at']} · {shown}")
    if vfx:
        if look["state"] != "approved":
            report.add("warn", "vfx", "the animation was authored without an approved "
                       "keyframe; no look was locked for it to inherit",
                       _cmd("keyframe", project) + ", approve it, then "
                       + _cmd("vfx", project, "--fresh"))
        elif _mtime(project.path("vfx")) < look["epoch"]:
            report.add("warn", "vfx", "vfx.json predates the approved keyframe; it was "
                       "authored against an earlier look", _cmd("vfx", project, "--fresh"))

    # -- sound -------------------------------------------------------------
    sound = read_json(project.source("sound"), warn=False) if "sound" in done else None
    if sound:
        cov = sound.get("coverage", {})
        review = sound.get("review") or {}
        flags = review.get("flags", [])
        blockers = [f for f in flags if f.get("severity") == "blocker"]
        report.metrics["sound"] = {"cues": len(sound.get("cues", [])),
                                   "music_share": cov.get("music_share"),
                                   "flags": len(flags)}
        report.add("ok", "sound", f"{len(sound.get('cues', []))} cues · "
                   f"{cov.get('music_share', 0)}% scored · longest run "
                   f"{cov.get('longest_music_run', 0)}s")
        # A cue the mix cannot place is a sound the film will not have:
        # fetch what a library has, synthesise the rest.
        from .sound import FETCH_KINDS
        no_file = [str(c.get("cue_id") or "?") for c in sound.get("cues", [])
                   if isinstance(c, dict) and c.get("kind") in FETCH_KINDS
                   and not (c.get("file") and (project.root / str(c["file"])).is_file())]
        if no_file:
            report.add("warn", "sound", f"{len(no_file)} cue(s) have no file on disk, "
                       "so the mix cannot place them: " + ", ".join(no_file[:8]),
                       _cmd("sound", project, "--fetch", "--synth"))
        if sound.get("failed_sections"):
            report.add("fail", "sound", f"{sound['failed_sections']} section(s) "
                       "failed to spot", _cmd("sound", project))
        # A cue sheet is spotted to absolute timecodes; if it was written
        # against the screenplay's estimate and the recording came in
        # shorter or longer, every cue after the first drift lands wrong.
        # A wordless film's voice_end is where its ending starts, not where a
        # narration stops: its cue sheet is measured against the film's end.
        wordless = bool(voice and voice.get("wordless"))
        ends = float((voice or {}).get("film_end" if wordless else "voice_end") or 0)
        if ends and sound.get("runtime"):
            spotted = float(sound["runtime"])
            if abs(spotted - ends) > ends * DRIFT_LIMIT:
                report.add("warn", "sound", f"the cue sheet is spotted for "
                           f"{timecode(spotted)} but the "
                           f"{'film' if wordless else 'narration'} ends at "
                           f"{timecode(ends)}; cues land on the wrong frames",
                           _cmd("retime", project) + ", then " + _cmd("sound", project))
        if (cov.get("music_share") or 0) > 85:
            report.add("warn", "sound", "music under more than 85% of the runtime; "
                       "it will stop being heard", "add scored silence; see the review")
        for flag in blockers:
            report.add("fail", "sound", f"{flag.get('cue', '?')}: "
                       f"{_clip(flag.get('problem', ''))}", _clip(flag.get("fix", "")))
        verdict = str(review.get("verdict", ""))
        if verdict:
            # The listen-back's one-line judgement is the thing an editor reads
            # first; a "not ready" is worth a warning even with no blocker.
            not_ready = any(w in verdict.lower() for w in ("not ready", "não está pronto"))
            report.add("warn" if not_ready else "note", "sound",
                       f"listen-back: {_clip(verdict)}",
                       _cmd("sound", project, "--review") if not_ready else "")
        important = [f for f in flags if f.get("severity") == "important"]
        if important:
            report.add("note", "sound", f"{len(important)} important listen-back "
                       "flag(s); see the Listen-back section of sound.md")

    # -- build / render ----------------------------------------------------
    build = read_json(project.source("build"), warn=False) if "build" in done else None
    if build:
        findings = build.get("findings", [])
        errors = [f for f in findings if str(f).startswith("error")]
        layers = build.get("layers", {})
        placeholders = layers.get("placeholder", 0)
        total = build.get("timeline", {}).get("shots", 0)
        report.metrics["build"] = {"shots": total, "placeholders": placeholders,
                                   "images": layers.get("image", 0),
                                   "findings": len(findings),
                                   "from_voice": build.get("timeline", {}).get("from_voice")}
        report.add("ok", "build", f"scene {build.get('scene', {}).get('version', '?')} · "
                   f"{total} shots · {layers.get('effect', 0)} effects, "
                   f"{layers.get('image', 0)} from files, "
                   f"{placeholders} placeholder cards")
        for error in errors:
            report.add("fail", "build", str(error)[:140], _cmd("build", project))
        if total and placeholders > total / 2:
            report.add("warn", "art", f"{placeholders} of {total} shots have no art "
                       "yet; the render is placeholder cards",
                       "produce the vfx stubs and assets, then " + _cmd("build", project))
        if (voice and voice.get("takes") and not voice.get("wordless")
                and not build.get("timeline", {}).get("from_voice")):
            report.add("warn", "build", "the scene was built from screenplay "
                       "estimates, but a narration exists", _cmd("build", project))
    if "render" in done:
        video = project.path("video")
        size = video.stat().st_size if video.exists() else 0
        report.metrics["render"] = {"bytes": size}
        report.add("ok" if size else "warn", "render",
                   f"video.mp4 {size / 1e6:.1f} MB" if size else "video.mp4 is empty")
        rep = read_json(project.source("render"), warn=False) or {}
        for output in (rep.get("result") or {}).get("outputs") or []:
            for finding in output.get("findings") or []:
                sev = str(finding.get("severity", "note")).lower()
                report.add("fail" if sev == "error" else "warn" if sev == "warning"
                           else "note", "render", str(finding.get("message", finding))[:140])
        # -- delivery: proven by files, never by build.json alone ----------
        missing = delivery_evidence(project, build)
        qa_done = read_json(project.root / "qa.json", warn=False) or {}
        report.metrics["delivery"] = {"ready": not missing,
                                      "missing": [m for m, _ in missing]}
        for problem, fix in missing:
            report.add("fail", "delivery", problem, fix)
        if qa_done.get("verdict") and qa_done.get("verdict") != READY_TO_PUBLISH:
            # QA names an owner per finding; the loop routes them back.
            report.add("note", "delivery", f"QA {qa_done.get('pass', '')} is not ready: "
                       "route its findings to their stages and re-run them",
                       _cmd("improve", project, "--dry-run"))
        if not missing:
            report.add("ok", "delivery", f"{READY_TO_PUBLISH}: a clean final render, "
                       f"gate deliverable, QA {qa_done.get('pass', '')} at "
                       f"{qa_done.get('overall', '?')}")

    # -- pilot -------------------------------------------------------------
    # The pilot writes its own document (segment outputs, schema raised to
    # 1.2), so it can fail where the plain scene cannot; its manifest carries
    # findings in build.json's shape, error-level ones prefixed "error".
    pilot = read_json(project.root / "pilot.json", warn=False)
    if pilot:
        findings = pilot.get("findings", [])
        errors = [f for f in findings if str(f).startswith("error")]
        cuts = pilot.get("cuts", {})
        report.metrics["pilot"] = {"cuts": sorted(cuts), "findings": len(findings)}
        report.add("ok", "pilot", f"{len(cuts)} cut(s): "
                   + ", ".join(f"{k}s covers {v.get('coverage', '?')}%"
                               for k, v in sorted(cuts.items(), key=lambda kv: int(kv[0]))))
        for error in errors:
            report.add("fail", "pilot", str(error)[:140], _cmd("pilot", project))

    # -- staleness ---------------------------------------------------------
    retimed = bool((script or {}).get("retimed_seconds"))
    for upstream, downstream, fix in STALE_PAIRS:
        if (upstream, downstream) == ("script", "voice") and retimed:
            continue    # the script was rewritten FROM the recording
        up, down = project.path(upstream), project.path(downstream)
        if up.exists() and down.exists() and _mtime(down) < _mtime(up):
            report.add("warn", "stale", f"{down.name} predates {up.name}; it "
                       "reflects an older cut", _cmd(fix.split()[1], project))

    if (project.root / "house.md").is_file():
        report.add("note", "house", "this video has its own house.md")

    # -- next steps --------------------------------------------------------
    # Commands only: what fixes a fail, what fixes a warn, then the stage that
    # has not run -- repairs before advancing, because a cue sheet spotted
    # against a drifting script is money spent twice. Prose advice stays
    # attached to its finding above, where it has its context.
    def commands(level: str) -> list[str]:
        return [f.fix for f in report.findings
                if f.level == level and f.fix.startswith("yt ")]

    pending = ""
    for stage, artifact in workspace.STAGES:
        if not project.has(artifact):
            extra = {"script": ("--polish", "--verify"), "sound": ("--review",),
                     "styles": ("--rich",)}.get(stage, ())
            pending = _cmd(stage, project, *extra)
            break
    else:
        pending = (_cmd("pilot", project) if not (project.root / "pilot.json").exists()
                   else _cmd("gate", project))
    prose = report.count("fail") - len(commands("fail"))
    for step in [*commands("fail"), *commands("warn"), pending]:
        if step and step not in report.next_steps:
            report.next_steps.append(step)
    if prose:
        report.next_steps.append(f"resolve the {prose} blocker(s) marked x above; "
                                 "they need a decision, not a command")
    return report


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def print_report(report: Report, *, title: str = "") -> None:
    print(f"\n{title or report.name.upper()}  —  {report.status} "
          f"({report.count('fail')} fail, {report.count('warn')} warn)")
    if report.metrics.get("stages_done") is not None:
        done = set(report.metrics["stages_done"])
        print("  " + "  ".join(("+ " if s in done else "- ") + s
                               for s, _ in workspace.STAGES))
    order = {level: n for n, level in enumerate(LEVELS)}
    for finding in sorted(report.findings, key=lambda f: order[f.level]):
        line = f"  {MARK[finding.level]} {finding.area:<11} {finding.message}"
        print(line)
        if finding.fix and finding.level in ("fail", "warn"):
            print(f"    {'':11} -> {finding.fix}")
    if report.next_steps:
        print("  next")
        for n, step in enumerate(report.next_steps[:6], 1):
            print(f"    {n}. {step}")


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "doctor", parents=list(parents),
        help="check the toolchain and every video, and say what to do next",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--title", help="one video, by a substring of its name")
    p.add_argument("--env", action="store_true", dest="env_only",
                   help="the environment only, no videos")
    p.add_argument("--json", dest="json_out", metavar="FILE",
                   help="also write every finding as JSON")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    work = workspace.work_dir(args)
    reports = [check_environment(work)]
    if not args.env_only:
        found = workspace.projects(work)
        if getattr(args, "project", None):
            found = [workspace.Project(Path(args.project))]
        elif args.title:
            found = workspace.matches(work, args.title)
            if not found:
                print(f"error: no video matches {args.title!r} in {work}", file=sys.stderr)
                return 1
        for project in found:
            reports.append(check_project(project))

    print_report(reports[0], title="ENVIRONMENT")
    for report in reports[1:]:
        print_report(report, title=report.metrics.get("title", report.name))

    fails = sum(r.count("fail") for r in reports)
    warns = sum(r.count("warn") for r in reports)
    print(f"\n{len(reports) - 1} video(s) · {fails} fail · {warns} warn", file=sys.stderr)
    if args.json_out:
        write_json(args.json_out, [
            {"name": r.name, "status": r.status, "metrics": r.metrics,
             "findings": [asdict(f) for f in r.findings], "next": r.next_steps}
            for r in reports])
        print(f"wrote {args.json_out}", file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="stage"),
             [argparse.ArgumentParser(add_help=False)])
    ns = parser.parse_args(["doctor", *sys.argv[1:]])
    ns.work, ns.quiet, ns.project = workspace.DEFAULT_WORK, False, None
    raise SystemExit(run(ns))
