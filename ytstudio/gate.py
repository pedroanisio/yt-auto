"""Put the rendered candidate through the delivery gate.

scene-render ships `tools/production_gate.py`, which checks an exact final
narrated candidate rather than a draft: it validates the document, resolves the
assets, measures the decoded loudness and true peak, runs the scopes (legal
range, gamut, chroma), looks for black and frozen frames, and checks the
caption timeline, line lengths and the 17-characters-per-second limit.

    yt gate --title monty              # the technical gate on the render
    yt gate --title monty --frames     # just the decoded frame check
    yt gate --title monty --preflight  # what the gate would need, nothing run
    yt gate --title monty --host root@h  # run it on that machine

This stage only arranges the arguments, because every one of them is something
an earlier stage already produced: the scene from `yt build`, the video and the
encode report from `yt render`, the captions and `voice_end` from `yt voice`.
A technical pass is not a perceptual one -- the gate says so itself. So the
report is handed to the model, which triages every finding into blocking or
acceptable, names the stage that owns the fix and the command that runs it,
and writes `gate.json`. A blocking item fails the gate even when the tool
passed; the tool's own failure is never overruled.

The gate needs the engine and ffprobe, and this workstation has neither by
policy (nothing is built here). So, like a render, it runs on a host: with
--host, or by default on the host an encode would go to when RS_RENDER_HOSTS
is set and the tools are not here. The candidate, its envelope and the two
tools directories are synced into a directory of the job's own, every path is
rewritten for the copy, the gate runs with the host's engine and ffprobe, its
output comes back, and the directory is removed. `--local` runs it here.

When the gate stops early -- it fails closed, on the first requirement that
does not hold -- the summary and gate.json say which requirement stopped it
and which stage owns it, so the fix starts in the right place.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import geometry, house, workspace
from . import render as render_stage
from .core import read_json, write_json
from .harness import Harness, HarnessError, brief

ROLE = (
    "You are the delivery supervisor. You read the technical gate's report on "
    "the rendered candidate -- loudness, true peak, scopes, black and frozen "
    "frames, caption timing -- together with what the build and the narration "
    "stages recorded, and you triage: what blocks publication, what is "
    "acceptable and why, which stage owns each fix and the exact `yt` command "
    "that runs it. A measurement that misses a platform target is blocking; a "
    "warning with no audible or visible consequence is acceptable, and you say "
    "what the consequence would be."
)

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "blocking": {"type": "array",
                     "items": {"type": "object",
                               "properties": {"what": {"type": "string"},
                                              "owner": {"type": "string",
                                                        "description": "the yt stage"},
                                              "fix": {"type": "string"}},
                               "required": ["what", "owner", "fix"]}},
        "acceptable": {"type": "array",
                       "items": {"type": "object",
                                 "properties": {"what": {"type": "string"},
                                                "why": {"type": "string"}},
                                 "required": ["what", "why"]}},
        "verdict": {"type": "string", "enum": ["deliverable", "not deliverable"]},
        "notes": {"type": "string"},
    },
    "required": ["blocking", "acceptable", "verdict", "notes"],
}


def exit_code(tool: int, triage: dict) -> int:
    """The tool's failure stands; a pass is only a pass with nothing blocking."""
    if tool:
        return tool
    return 1 if triage.get("blocking") else 0


PENETRATES = "hero penetrates set"
READS_MERGED = "hero reads merged into set"


def engine_geometry(args, project, scene: str, shots: list[dict]) -> dict:
    """The geometry check over the candidate's scene at `geometry.STEP`
    steps through every shot (the whole programme with no build): the
    engine evaluates the frame graph on a render host, one job."""
    return geometry.check_project(Path(scene), project.root, shots,
                                  host=render_stage.remote_host(args, "validate"))


#: The seam: what `run` checks the geometry with. A test puts a fake here.
GEOMETRY = engine_geometry


def geometry_rows(report: dict | None) -> list[dict]:
    """The geometry report as blocking triage rows: one per hero and set
    object, with the worst instance, how many samples and the span, owned
    by vfx. Nothing when the check found nothing or could not run."""
    if not report or report.get("status") != "ok":
        return []
    rows = []
    for w in report.get("worst") or []:
        short = lambda s: str(s).rsplit("/", 1)[-1]  # noqa: E731
        rows.append({
            "what": f"{PENETRATES}: `{short(w['hero'])}` inside `{short(w['object'])}` by "
                    f"{w['depth']:.1f} units along {w['axis']} at {w['time']:g}s, "
                    f"{w['count']} of {len(report.get('times') or [])} samples between "
                    f"{w['first']:g}s and {w['last']:g}s (tolerance {w['tolerance']:.1f}"
                    + ("; bounds approximate" if w.get("approximate") else "") + ")",
            "owner": "vfx",
            "fix": f"`yt vfx` (the authored world): {geometry.advice(w, rests=report.get('rests') or [])}; "
                   "then `yt build` and `yt render`"})
    for f in report.get("floating") or []:
        rows.append({
            "what": f"hero floats above its rest: `{str(f['hero']).rsplit('/', 1)[-1]}` is "
                    f"{f['gap']:.1f} units above `{str(f['surface']).rsplit('/', 1)[-1]}` at "
                    f"{f['time']:g}s where it is declared to rest",
            "owner": "vfx",
            "fix": "`yt vfx`: lower the final keys so the hero touches the surface; "
                   "then `yt build` and `yt render`"})
    for m in report.get("reads_merged") or []:
        rows.append({
            "what": f"{READS_MERGED}: `{str(m['hero']).rsplit('/', 1)[-1]}` over "
                    f"`{str(m['object']).rsplit('/', 1)[-1]}` from {m['first']:g}s to "
                    f"{m['last']:g}s ({m['seconds']:g}s): {m['overlap']:.0%} of the hero over the "
                    f"surface on screen, {m['clearance']:.1f} units above it ({m['ratio']:.0%} of "
                    "the camera distance), no shadow on it",
            "owner": "vfx",
            "fix": f"`yt vfx` (the authored world): {geometry.merged_advice(m)}; "
                   "then `yt build` and `yt render`"})
    return rows


def apply_geometry(triage: dict, report: dict | None) -> dict:
    """The mechanical verdict over the supervisor's: a penetration is
    blocking whatever the triage said, so a film with a merge cannot pass."""
    rows = geometry_rows(report)
    if not rows:
        return triage
    out = dict(triage)
    have = {r.get("what") for r in out.get("blocking") or []}
    out["blocking"] = list(out.get("blocking") or []) + [r for r in rows if r["what"] not in have]
    out["verdict"] = "not deliverable"
    return out

GATE_HINT = ("production_gate.py was not found. Set PRODUCTION_GATE to it, or "
             "keep the scene-render checkout beside this one.")
TOOLS_HINT = ("the production audio tools were not found. Pass --tools-dir, or "
              "set YT_TOOLS_DIR.")
HOST_HINT = ("--host USER@HOST, or set RS_RENDER_HOSTS: the render hosts have "
             "it; nothing is built or installed here")

#: Where a gate job lives on the host: REMOTE_GATE_ROOT/<slug>/gate-<tag>/.
REMOTE_GATE_ROOT = "/tmp/yt-gate"

#: What the project copy leaves behind: review by-products and other cuts.
#: The candidate is included by name before these, so a draft under test
#: still travels.
GATE_EXCLUDES = render_stage.SYNC_EXCLUDES + (
    "qa/", "decisions/", "*.pdf", "*.wav", "slice-*.mp4", "pilot*.mp4", "video-*.mp4")


@dataclass
class RemotePlan:
    """Everything a remote gate run does, as a value: no ssh in making it."""
    host: str
    root: str                               # the job's directory on the host
    project: str                            # the project copy inside it
    engine: str                             # the engine binary on the host
    local_root: str = ""                    # the project here, resolved
    paths: dict[str, str] = field(default_factory=dict)   # local input -> remote path
    syncs: list[list[str]] = field(default_factory=list)  # rsync argv, in order
    command: list[str] = field(default_factory=list)      # production_gate.py argv there

    @property
    def envelope(self) -> str:
        return f"{self.root}/inputs/render.json"


def _remote_path(local: str, project_root: Path, plan: RemotePlan) -> str:
    """Inside the project, the same place in the copy; outside, inputs/<name>."""
    try:
        rel = Path(local).resolve().relative_to(project_root.resolve())
        return f"{plan.project}/{rel.as_posix()}"
    except ValueError:
        return f"{plan.root}/inputs/{Path(local).name}"


def plan_remote(found: dict, extras: list[str], project_root: Path, host: str, *,
                tag: str, frames: bool = False, loudness_target: float = -14.0,
                max_illegal: float | None = None) -> RemotePlan:
    """Map every input to the host and assemble the syncs and the command.

    `found` is what inputs() collected (its `engine` is the host's binary);
    `extras` are the extra caption files. The envelope always travels as the
    rewritten copy in inputs/ (see remote_envelope), never the project's own.
    """
    slug = project_root.resolve().name
    root = f"{REMOTE_GATE_ROOT}/{slug}/gate-{tag}"
    plan = RemotePlan(host=host, root=root, project=f"{root}/{slug}", engine=found["engine"],
                      local_root=str(project_root.resolve()))
    gate_dir = Path(found["gate"]).parent
    tools = Path(found["tools dir"])
    captions = [c for c in [found.get("captions"), *extras] if c]
    for local in [found["scene"], found["video"], *captions]:
        plan.paths[local] = _remote_path(local, project_root, plan)
    plan.paths[found["encode report"]] = plan.envelope

    # the candidate by name, before the patterns that drop other cuts
    includes: tuple[str, ...] = ()
    video_remote = plan.paths[found["video"]]
    if video_remote.startswith(plan.project + "/"):
        includes = (video_remote[len(plan.project) + 1:],)
    plan.syncs.append(render_stage.sync_up_args(
        project_root, host, plan.project, delete=False, excludes=GATE_EXCLUDES, includes=includes))
    plan.syncs.append(render_stage.sync_up_args(
        gate_dir, host, f"{root}/tools/gate", delete=False,
        excludes=("__pycache__/", "fixtures/", "evidence/", "*.md")))
    plan.syncs.append(render_stage.sync_up_args(
        tools, host, f"{root}/tools/audio", delete=False, excludes=("__pycache__/",)))
    if found.get("audio tools"):
        # the bundled sr-* tools merge into the same directory, after the tools dir's own files
        plan.syncs.append(render_stage.sync_up_args(
            Path(found["audio tools"]), host, f"{root}/tools/audio", delete=False,
            excludes=("__pycache__/",)))
    for local, remote in plan.paths.items():
        if remote.startswith(root + "/inputs/") and local != found["encode report"]:
            plan.syncs.append(render_stage.sync_up_args(
                Path(local), host, remote, file=True, delete=False, excludes=()))

    gate_remote = f"{root}/tools/gate/{Path(found['gate']).name}"
    if frames:
        plan.command = ["python3", gate_remote, "frames", plan.envelope]
        return plan
    plan.command = ["python3", gate_remote, "technical", plan.project,
                    "--scene", plan.paths[found["scene"]], "--video", video_remote,
                    "--engine", plan.engine, "--encode-report", plan.envelope,
                    "--tools-dir", f"{root}/tools/audio",
                    "--voice-end", str(found["voice end"]),
                    "--loudness-target", str(loudness_target)]
    for caption in captions:
        plan.command += ["--captions", plan.paths[caption]]
    if (tools / "tail_check.py").is_file() or not tools.is_dir():
        plan.command += ["--tail-check", f"{root}/tools/audio/tail_check.py"]
    if max_illegal is not None:
        plan.command += ["--max-illegal", str(max_illegal)]
    return plan


def remote_envelope(report: dict, plan: RemotePlan) -> dict:
    """The encode report as the host must read it: outputs[].path must equal
    the candidate's path there, so every project path is moved under the copy
    and every input shipped from outside is renamed to its inputs/ copy."""
    out = render_stage._localise(report, plan.local_root, plan.project)
    for local, remote in plan.paths.items():
        if remote.startswith(plan.root + "/inputs/"):
            out = _replace(out, str(Path(local).resolve()), remote)
            out = _replace(out, local, remote)
    return out


def _replace(node, old: str, new: str):
    if isinstance(node, dict):
        return {k: _replace(v, old, new) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace(v, old, new) for v in node]
    return new if node == old else node


def remote_script(plan: RemotePlan) -> str:
    """The one-line shell the host runs: inside the job directory, the gate."""
    return (f"cd {shlex.quote(plan.root)} && "
            + " ".join(shlex.quote(t) for t in plan.command))


def run_remote(plan: RemotePlan, report: dict, project_root: Path, *, verbose: bool,
               keep_remote: bool) -> subprocess.CompletedProcess:
    """Ship, run, bring the gate's own JSON back, remove the directory."""
    if verbose:
        print(f"  sync -> {plan.host}:{plan.root}", file=sys.stderr)
    render_stage.refuse_remote()
    try:
        for argv in plan.syncs:
            subprocess.run(argv, check=True, capture_output=True, text=True)
        envelope = json.dumps(remote_envelope(report, plan), indent=2, ensure_ascii=False) + "\n"
        subprocess.run(["ssh", "-o", "BatchMode=yes", plan.host,
                        f"mkdir -p {shlex.quote(plan.root + '/inputs')} && cat > {shlex.quote(plan.envelope)}"],
                       input=envelope, check=True, capture_output=True, text=True)
        script = remote_script(plan)
        if verbose:
            print(f"  ssh {plan.host} {script}", file=sys.stderr)
        proc = subprocess.run(["ssh", "-o", "BatchMode=yes", plan.host, script],
                              capture_output=True, text=True)
        # The gate writes qa/TECHNICAL.json into the project copy on a pass;
        # bring it home with its paths localised, like a render report.
        technical = project_root / "qa" / "TECHNICAL.json"
        technical.parent.mkdir(parents=True, exist_ok=True)
        render_stage.refuse_remote()
        pulled = subprocess.run(["rsync", "-az", f"{plan.host}:{plan.project}/qa/TECHNICAL.json",
                                 str(technical)], capture_output=True, text=True)
        if pulled.returncode == 0 and technical.is_file():
            data = read_json(technical, warn=False)
            if isinstance(data, dict):
                write_json(technical, render_stage._localise(
                    data, plan.project, str(project_root.resolve())))
            if verbose:
                print(f"wrote {technical}", file=sys.stderr)
    finally:
        if keep_remote:
            if verbose:
                print(f"  kept {plan.host}:{plan.root}", file=sys.stderr)
        else:
            render_stage.remove_remote(plan.host, plan.root, verbose=verbose)
    return proc


def gate_host(args, *, have_local: bool | None = None) -> str:
    """Where the gate runs: --host, else here when the engine and ffprobe are
    here (or --local), else the host an encode would go to; "" is here."""
    if getattr(args, "local", False):
        return ""
    if getattr(args, "host", None):
        return args.host
    if have_local is None:
        have_local = bool(render_stage.engine() and shutil.which("ffprobe"))
    if have_local:
        return ""
    return render_stage.remote_host(args, "encode")


#: What production_gate.py checks, in the order it stops: (message prefix,
#: requirement, owning stage, the fix). It fails closed on the first
#: requirement that does not hold, so one line names where the work is.
STOPS = (
    ("Encode report must identify the candidate", "identification", "render",
     "the envelope's outputs[].path must name the candidate: `yt render` writes it "
     "localised; pass --render/--video to the pair that belong together"),
    ("Encode report must be the", "envelope", "render",
     "`yt render` keeps the engine's scene-render-cli/1 envelope as render.json"),
    ("Encode report is not one JSON", "envelope", "render", "`yt render` again"),
    ("Encode report is not a successful", "envelope", "render", "`yt render` again, to exit 0"),
    ("Encode report has no result.outputs", "envelope", "render", "`yt render` again"),
    ("Encode report has evaluation_warnings", "evaluation_warnings", "build",
     "the document evaluates with warnings (an include that declares what the including "
     "document owns, for one): fix the source `yt build` compiles, then `yt build` and `yt render`"),
    ("Encode report has warnings", "warnings", "vfx",
     "the engine warned while rendering (objects drawn off camera, for one): fix the authored "
     "documents, then `yt build` and `yt render`"),
    ("Encode report has unsupported", "unsupported", "vfx",
     "the documents use what the engine does not support; `yt vfx` and `yt build`"),
    ("Encode report has accessibility", "accessibility", "build", "`yt build`, then `yt render`"),
    ("Encode report has uploads", "uploads", "render", "`yt render` passes --no-upload"),
    ("Publication requires final quality", "final_quality", "render", "`yt render` (no --quality draft)"),
    ("Frame count mismatch", "frame_count", "render", "`yt render` again; the file and the report disagree"),
    ("Encoded dimensions differ", "dimensions", "render", "`yt render` again"),
    ("Encoded FPS mismatch", "fps", "render", "`yt render` again"),
    ("Video/audio duration mismatch", "duration", "render", "`yt render` again"),
    ("Expected one video stream", "video_stream", "render", "`yt render` again"),
    ("Last spoken word must end", "voice_end", "voice",
     "voice_end is past the film's end: `yt voice`, or --voice-end"),
    ("Narrated film needs audio", "audio_stream", "sound", "the film has no audio track: `yt sound`, `yt build`"),
    ("Scene must belong to project", "scene", "build", "`yt build` writes scene.xml in the project"),
    ("Vendor ", "assets", "build", "assets must live inside the project: `yt assets`, `yt build`"),
    ("Asset outside project", "assets", "build", "assets must live inside the project: `yt assets`, `yt build`"),
    ("Missing/empty artifact", "artifact", "render", "the named file is missing or empty"),
    ("No caption cues found", "captions", "voice", "`yt voice` writes narration.vtt (for a wordless film too: "
     "descriptive captions)"),
    ("Caption", "captions", "voice", "`yt voice`"),
    ("Malformed caption", "captions", "voice", "`yt voice`"),
    ("Overlapping or unordered caption", "captions", "voice", "`yt voice`"),
    ("Invalid caption", "captions", "voice", "`yt voice`"),
    ("Final captions must be VTT", "captions", "voice", "`yt voice`"),
    ("Decoded loudness", "loudness", "build", "`yt build --loudness`, then `yt render`"),
    ("Decoded true peak", "true_peak", "build", "`yt build`, then `yt render`"),
    ("Cannot measure decoded loudness", "ffmpeg", "environment", "ffmpeg on the gate host, or " + HOST_HINT),
    ("Scopes out of tolerance", "scopes", "vfx", "the pictures leave the legal range: `yt vfx`, `yt build`"),
    ("Scopes analysed no frames", "scopes", "render", "`yt render` again"),
    ("Project changed during", "stability", "gate", "run `yt gate` again with nothing else writing the project"),
    ("Candidate changed during", "stability", "gate", "run `yt gate` again with nothing else writing the project"),
    ("No such file or directory", "tools", "environment", HOST_HINT),
)

#: A "Check failed" names the tool that ran; the tool names the owner.
CHECK_OWNERS = (
    (("validate", "resolve"), "build", "`yt build`, then `yt render`"),
    (("sr-audiocheck", "sr-margin", "sr-sibilance", "tail_check"), "sound", "`yt sound`, then `yt build`"),
    (("ffmpeg", "ffprobe"), "render", "`yt render` again"),
)


def stopped_by(output: str) -> dict | None:
    """Which requirement stopped the gate, from its output, and who owns it.

    None when it did not stop (a pass, or no NOT READY line)."""
    line = next((l.strip() for l in output.splitlines() if l.strip().startswith("NOT READY:")), "")
    if not line:
        return None
    message = line[len("NOT READY:"):].strip()
    if message.startswith("Check failed"):
        command = message.split(":", 1)[-1].strip().split("\n", 1)[0]
        tool = Path(command.split()[0]).name if command.split() else "?"
        if tool.startswith("python") and len(command.split()) > 1:
            tool = Path(command.split()[1]).name
        requirement = tool.rsplit(".", 1)[0]
        # the engine's own subcommands are the requirement, not the binary
        if tool == "scene-render" and len(command.split()) > 1:
            requirement = command.split()[1]
        for names, owner, fix in CHECK_OWNERS:
            if requirement in names:
                return {"requirement": requirement, "message": message, "owner": owner, "fix": fix}
        return {"requirement": requirement, "message": message, "owner": "?", "fix": "see the tool's output"}
    for prefix, requirement, owner, fix in STOPS:
        if message.startswith(prefix) or (prefix.startswith("No such file") and prefix in message):
            return {"requirement": requirement, "message": message, "owner": owner, "fix": fix}
    return {"requirement": message, "message": message, "owner": "?", "fix": "see the tool's output"}


def gate_script() -> Path | None:
    env = os.environ.get("PRODUCTION_GATE")
    if env and Path(env).is_file():
        return Path(env)
    here = Path(__file__).resolve().parent.parent
    for root in (here.parent / "scene-render", Path.home() / "src/scene-render"):
        candidate = root / "tools" / "production_gate.py"
        if candidate.is_file():
            return candidate
    return None


def tools_dir(explicit: str | None) -> Path | None:
    here = Path(__file__).resolve().parent.parent
    for candidate in ([Path(explicit)] if explicit else []) + (
            [Path(os.environ["YT_TOOLS_DIR"])] if os.environ.get("YT_TOOLS_DIR")
            else []) + [here.parent / "pnkit" / "tools",
                        Path.home() / "src/pnkit/tools"]:
        if candidate.is_dir():
            return candidate
    return None


#: The three tools production_gate.py runs from --tools-dir, by name.
AUDIO_TOOLS = ("sr-audiocheck", "sr-margin", "sr-sibilance")
AUDIO_TOOLS_HINT = ("bundled in ytstudio/audiotools; restore the file from the repo, or "
                    "put one of that name in --tools-dir")


def bundled_tools() -> Path:
    """The audio tools that ship with yt (stdlib python and ffmpeg; see audiotools/sr_audio.py)."""
    return Path(__file__).resolve().parent / "audiotools"


def ensure_executable(directory: Path) -> None:
    """production_gate.py runs <tools-dir>/<name> directly, so the tools need the
    execute bit, which a zip or a filesystem without modes drops."""
    for name in AUDIO_TOOLS:
        path = directory / name
        try:
            if path.is_file() and not os.access(path, os.X_OK):
                path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            pass


def audio_tool_paths(tools: Path | None) -> dict[str, str]:
    """Where each audio tool is found (the tools dir first, then the bundle); "" when nowhere."""
    found = {}
    for name in AUDIO_TOOLS:
        candidates = ([tools / name] if tools else []) + [bundled_tools() / name]
        found[name] = next((str(c) for c in candidates if c.is_file()), "")
    return found


def audio_tools_to_ship(tools: Path | None) -> Path | None:
    """The bundle, when the tools dir lacks any of the three; None when it has them all."""
    if tools and all((tools / name).is_file() for name in AUDIO_TOOLS):
        return None
    return bundled_tools() if bundled_tools().is_dir() else None


def inputs(project, args, host: str = "") -> tuple[dict, list[str]]:
    """Everything the gate needs, and what is still missing.

    Inside the pipeline every one of these is in the project; standalone, each
    is a flag: --scene, --video, --render, --captions (the first is the
    track under test, the rest extras), --voice or --voice-end. The engine
    and ffprobe are the host's when the gate runs on one.
    """
    voice = read_json(project.source("voice", workspace.override(args, "voice")),
                      warn=False) or {}
    captions = project.source("captions")
    extras = list(args.captions)
    if not captions and extras:
        captions, extras = extras[0], extras[1:]
    args.captions = extras
    voice_end = getattr(args, "voice_end", None)
    if host:
        known = render_stage.probe(host)
        engine, ffprobe = known.engine, known.ffprobe
    else:
        engine, ffprobe = str(render_stage.engine() or ""), shutil.which("ffprobe") or ""
    found = {
        "scene": project.source("scene", workspace.override(args, "scene")),
        "video": project.source("video", args.video),
        "encode report": project.source("render", workspace.override(args, "render")),
        "captions": captions,
        "engine": engine,
        "ffprobe": ffprobe,
        "gate": str(gate_script() or ""),
        "tools dir": str(tools_dir(args.tools_dir) or ""),
        "voice end": voice_end if voice_end is not None else voice.get("voice_end"),
    }
    # A voice end of 0 is a value (a wordless one-shot film), not an absence.
    missing = [name for name, value in found.items()
               if not value and not (name == "voice end" and value == 0)]
    # each audio tool by name, so the preflight and the run name the one that is missing
    tools = tools_dir(args.tools_dir)
    shipped = audio_tools_to_ship(tools)
    if shipped:
        ensure_executable(shipped)
        found["audio tools"] = str(shipped)
    paths = audio_tool_paths(tools)
    found.update(paths)
    missing += [name for name, where in paths.items() if not where]
    return found, missing


def local_command(found: dict, extras: list[str], *, frames: bool, loudness_target: float,
                  max_illegal: float | None) -> list[str]:
    """production_gate.py's argv for a run on this machine."""
    script = found["gate"]
    if frames:
        return [sys.executable, script, "frames", found["encode report"]]
    cmd = [sys.executable, script, "technical", str(found["project"]),
           "--scene", found["scene"], "--video", found["video"],
           "--engine", found["engine"],
           "--encode-report", found["encode report"],
           "--tools-dir", found.get("audio tools") or found["tools dir"],
           "--voice-end", str(found["voice end"]),
           "--loudness-target", str(loudness_target)]
    for caption in [found["captions"], *extras]:
        cmd += ["--captions", caption]
    tail = Path(found["tools dir"]) / "tail_check.py"
    if tail.is_file():
        cmd += ["--tail-check", str(tail)]
    if max_illegal is not None:
        cmd += ["--max-illegal", str(max_illegal)]
    return cmd


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "gate", parents=list(parents),
        help="run the delivery gate over the rendered candidate",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", metavar="FILE",
                   help="the candidate (default: the project's video.mp4)")
    p.add_argument("--tools-dir", dest="tools_dir", metavar="DIR",
                   help="the production audio tools")
    p.add_argument("--captions", action="append", default=[], metavar="FILE",
                   help="extra caption files to check (repeatable)")
    p.add_argument("--loudness-target", type=float, default=-14.0,
                   dest="loudness_target", help="integrated LUFS target (-14)")
    p.add_argument("--max-illegal", type=float, dest="max_illegal",
                   help="largest fraction of samples outside the legal range")
    p.add_argument("--frames", action="store_true",
                   help="only check the decoded frames against the report")
    p.add_argument("--preflight", action="store_true",
                   help="list what the gate needs and run nothing")
    p.add_argument("--fresh", action="store_true",
                   help="triage again even if the report did not change")
    p.add_argument("--voice-end", type=float, dest="voice_end", metavar="SECONDS",
                   help="when the narration ends (default: from voice.json)")
    p.add_argument("--host", metavar="USER@HOST",
                   help="run the gate on this machine over ssh (default: where an "
                        "encode would go, when the engine and ffprobe are not here)")
    p.add_argument("--local", action="store_true",
                   help="run the gate here even when render hosts are configured")
    p.add_argument("--keep-remote", action="store_true", dest="keep_remote",
                   help="leave the job's directory on the host for debugging")
    workspace.add_inputs(p, "scene", "render", "voice", "build")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    idea, project = workspace.resolve(args, inputs=("video", "scene"))
    host = gate_host(args)
    found, missing = inputs(project, args, host)

    if args.preflight or missing:
        print(f"\n{idea.title} — DELIVERY GATE")
        print(f"  {'ok ' if host else '-  '}{'host':<14} {host or 'this machine'}")
        for name, value in found.items():
            mark = "ok " if value else "-  "
            shown = f"{host}:{value}" if host and name in ("engine", "ffprobe") and value else value
            print(f"  {mark}{name:<14} {shown if value else 'missing'}")
        if missing:
            how = {"video": "yt render, or --video FILE",
                   "encode report": "yt render, or --render FILE",
                   "captions": "yt voice (a wordless film too), or --captions FILE",
                   "voice end": "yt voice (a wordless film too: the last shot's "
                                "start), or --voice FILE / --voice-end SECONDS",
                   "scene": "yt build, or --scene FILE",
                   "engine": HOST_HINT, "ffprobe": HOST_HINT,
                   "gate": GATE_HINT, "tools dir": TOOLS_HINT,
                   **{name: AUDIO_TOOLS_HINT for name in AUDIO_TOOLS}}
            print("\n  to complete it:")
            for name in missing:
                print(f"    {name:<14} {how.get(name, '?')}")
        if args.preflight:
            return 0
        print("\nerror: the gate cannot run yet", file=sys.stderr)
        return 1

    render = read_json(found["encode report"], warn=False) or {}
    if host:
        plan = plan_remote(found, args.captions, project.root, host, tag=render_stage.job_tag(),
                           frames=args.frames, loudness_target=args.loudness_target,
                           max_illegal=args.max_illegal)
        cmd = plan.command
        if not args.quiet:
            print("  " + " ".join(cmd), file=sys.stderr)
        try:
            proc = run_remote(plan, render if isinstance(render, dict) else {}, project.root,
                              verbose=not args.quiet, keep_remote=args.keep_remote)
        except subprocess.CalledProcessError as exc:
            print(f"error: could not reach {host}: {(exc.stderr or '').strip()[:300]}",
                  file=sys.stderr)
            return 1
    else:
        cmd = local_command(found | {"project": project.root}, args.captions, frames=args.frames,
                            loudness_target=args.loudness_target, max_illegal=args.max_illegal)
        if not args.quiet:
            print("  " + " ".join(cmd), file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    for stream in (proc.stdout, proc.stderr):
        if stream.strip():
            print(stream.strip()[-6000:])
    verdict = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"\n{idea.title} — gate: {verdict} (exit {proc.returncode})"
          + (f" on {host}" if host else ""))
    stop = stopped_by(output)
    if stop:
        print(f"  stopped at: {stop['requirement']} — {stop['message'][:160]}")
        print(f"              owner {stop['owner']}: {stop['fix'][:160]}")

    # The tool measured; the supervisor decides what each measurement means.
    build = read_json(project.source("build", workspace.override(args, "build")),
                      warn=False) or {}
    voice = read_json(project.source("voice", workspace.override(args, "voice")),
                      warn=False) or {}
    # The tool has no notion of a solid: before anyone triages, the geometry
    # check says whether the hero passes through the set. Blocking on its own.
    geo = GEOMETRY(args, project, found["scene"], build.get("shots", []))
    if geo.get("status") != "ok":
        print(f"  geometry: not checked ({geo.get('why', '?')})", file=sys.stderr)
    for row in geometry_rows(geo):
        print(f"  BLOCKING  {row['what'][:160]}")
    rules, digest = house.for_args(args, project, "gate")
    supervisor = Harness(project.root, "gate", model=args.model, role=ROLE,
                         house=rules, house_digest=digest,
                         fresh=getattr(args, "fresh", False), verbose=not args.quiet)
    try:
        triage = supervisor.decide("triage", "\n\n".join([
            f"Triage the delivery gate's report. The tool exited {proc.returncode} "
            f"({verdict}); mode: {'frames only' if args.frames else 'technical'}; "
            f"loudness target {args.loudness_target} LUFS; ran on "
            f"{host or 'this machine'} with its engine and ffprobe.",
            brief("Gate output", output[-8000:]),
            brief("Stopped by (the tool fails closed on the first requirement that does not "
                  "hold; later checks did not run)", stop or "nothing: the tool ran to the end"),
            brief("Build findings", build.get("findings", [])),
            brief("Build editorial", build.get("editorial", {})),
            brief("Placeholders", build.get("placeholders", [])),
            brief("Narration drift", {"drift_seconds": voice.get("drift_seconds"),
                                      "too_fast_for_captions": voice.get("too_fast_for_captions", []),
                                      "overlaps": voice.get("overlaps", [])}),
            brief("Encode report (summary)", render_stage.summarise(render)
                  if isinstance(render, dict) else render),
            brief("Geometry check (mechanical: every penetration of a hero into a set solid, "
                  "and every span where the hero reads merged into a surface on "
                  "screen, is appended to `blocking` as an item of its own, owner vfx, after your "
                  "triage; do not restate it and do not overrule it, but let it inform the "
                  "verdict and the order of the fixes)",
                  {k: geo.get(k) for k in ("status", "why", "evaluated", "worst", "floating",
                                           "reads_merged")}),
        ]), TRIAGE_SCHEMA)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    triage = apply_geometry(triage, geo)
    print(f"\n  supervisor: {triage['verdict']}")
    for row in triage.get("blocking", []):
        print(f"    BLOCKING  {row.get('what', '')[:90]}")
        print(f"              owner {row.get('owner', '?')}: {row.get('fix', '')[:90]}")
    for row in triage.get("acceptable", [])[:8]:
        print(f"    ok        {row.get('what', '')[:70]} -- {row.get('why', '')[:60]}")
    if triage.get("notes"):
        print(f"  notes: {triage['notes'][:300]}")
    code = exit_code(proc.returncode, triage)
    gate_out = (Path(found["video"]).with_name("gate.json") if project.standalone
                else project.root / "gate.json")
    write_json(gate_out, {
        "idea": {"title": idea.title}, "tool_exit": proc.returncode,
        "mode": "frames" if args.frames else "technical", "host": host or "",
        "command": cmd, "stopped_by": stop, "tool_output": output[-4000:],
        "triage": triage, "geometry": geo, "exit": code, "engine": supervisor.report()})
    print(f"wrote {gate_out}", file=sys.stderr)
    if code == 0 and not args.frames:
        print("a perceptual review is still required: this gate is technical; "
              "run `yt qa`")
    return code
