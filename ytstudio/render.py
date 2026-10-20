"""Drive the scene-render engine over the built document.

A thin layer on purpose: the engine validates, evaluates, composites and
encodes, and reports what it did as JSON. This finds the binary, runs it, keeps
the report in the project and reads the findings back out, so a render that
technically succeeded but produced a warning does not quietly pass.

    yt render --title monty                     # encode video.mp4
    yt render --title monty --quality draft     # half-size internals, fast
    yt render --title monty --still 12.5        # one frame, to look at
    yt render --title monty --start 60 --end 90 # just that minute
    yt render --title monty --validate          # the engine's own validation

The engine is Rust and is not bundled. It is found through RS_RENDER_BIN, on
PATH as `scene-render`, or in a checkout beside this one -- or it runs on
other machines: with RS_RENDER_HOSTS (comma-separated ssh targets; RS_RENDER_HOST
for one) or --host set, the project is mirrored there once and refreshed
incrementally, each job runs in a hardlinked directory of its own (so two
jobs on one project never overwrite each other), and the video, stills and
report are synced back. `--keep-remote` keeps a job's directory for a look;
`--prune-remote` deletes the project's mirror. Nothing is built or installed
here.

Which machine gets a job follows docs/compute.md: memory, not GPU. A full
encode and the audio mix go to the host with the most RAM; a still, a frame
range or a short block to the one with the least, which is the faster GPU.
`--slices N` splits a long encode across every host that has the engine --
one --start/--end range per slice, the audio mix rendered once on the big
host, then a stream-copy concat and a mux there -- because `--parallel` only
splits within one machine.

    RS_RENDER_HOSTS=root@192.168.199.7,root@192.168.199.13 yt render --title monty
    yt render --title monty --quality draft --still 12.5      # the fast GPU
    yt render --title monty --slices 4                        # both machines
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import workspace
from .core import read_json, write_json

BUILD_HINT = ("the scene-render binary was not found. Set RS_RENDER_BIN to it, "
              "put `scene-render` on PATH, or build the checkout: "
              "cd ../scene-render && cargo build --release")


def engine() -> Path | None:
    """The engine binary, wherever this machine keeps it."""
    env = os.environ.get("RS_RENDER_BIN")
    if env and Path(env).is_file():
        return Path(env)
    found = shutil.which("scene-render")
    if found:
        return Path(found)
    here = Path(__file__).resolve().parent.parent
    for candidate in (here.parent / "scene-render", Path.home() / "src/scene-render"):
        binary = candidate / "target" / "release" / "scene-render"
        if binary.is_file():
            return binary
    return None


#: Where a project lives on a render host: REMOTE_ROOT/<slug>/mirror/ is the
#: one persistent, incrementally synced copy; each job runs in its own
#: REMOTE_ROOT/<slug>/<job>-<tag>/, hardlinked from the mirror in a second,
#: and is removed once its results are back. The mirror stays until
#: --prune-remote.
REMOTE_ROOT = "/tmp/yt-render"

#: Scratch roots a job may remove a directory under -- and nothing else.
SCRATCH_ROOTS = (REMOTE_ROOT, "/tmp/yt-gate")

#: Candidate engine paths on the host, when RS_RENDER_REMOTE_BIN is not set.
REMOTE_BINS = ("scene-render", "$HOME/src/scene-render/target/release/scene-render")

#: What never travels to the host: what no render needs -- caches, downloads,
#: review by-products, this project's own rendered outputs and the proof
#: frames. props/ and sound/ travel: the scene references them.
SYNC_EXCLUDES = (".cache/", "*.bak", "raw/", "review-frames/", "vfx-stubs/", "qa/",
                 "decisions/", "video*.mp4", "pilot*.mp4", "/mix.wav",
                 "/*.png", "/*.pdf", "vfx/frames/", "vfx/v*/frames/", ".estimate-*")

#: What comes back from the host after a run: results, never the inputs.
PULL_BACK = ("*.mp4", "*.png", "*.wav", "render.json", "render-*.json", "pilot*.json",
             ".estimate-*.json")


#: Every ssh this module runs: no prompts, and a dead link is noticed in about a
#: minute instead of never, so a session that drops is seen as dropped.
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4")
SSH_COMMAND = "ssh " + " ".join(SSH_OPTS)      # rsync -e


#: Set by the test runner: nothing in ytstudio may start ssh or rsync, and no host is
#: configured, whatever the repo's .env names.
NO_REMOTE_ENV = "YT_NO_REMOTE"


class RemoteRefused(RuntimeError):
    """A launcher tried to reach a render host while YT_NO_REMOTE is set."""


def refuse_remote(what: str = "ssh") -> None:
    """Call before any ssh/rsync launch: raises under YT_NO_REMOTE, so tests never reach a host."""
    if os.environ.get(NO_REMOTE_ENV):
        raise RemoteRefused(f"{what} refused: {NO_REMOTE_ENV} is set (tests never reach a render host)")


def ssh_argv(host: str, script: str, *extra: str) -> list[str]:
    """argv for one ssh call: the keepalive options, `extra` (-o ...), the host, the script."""
    return ["ssh", *SSH_OPTS, *extra, host, script]


def probe_argv(host: str, script: str, timeout: int = 4) -> list[str]:
    """ssh argv for a short check: a connection not made in `timeout` s is a no."""
    return ssh_argv(host, script, "-o", f"ConnectTimeout={timeout}")


def job_tag() -> str:
    """Unique per job: the pid traces a stray directory to its process, the
    suffix keeps two jobs of one process (or two machines) apart."""
    return f"{os.getpid()}-{uuid.uuid4().hex[:6]}"


def remote_dir(slug: str, job: str, tag: str, root: str = REMOTE_ROOT) -> str:
    """The directory one job owns on the host. Two jobs on one project never
    share one: that is how a pilot's sync overwrote a finished film once."""
    return f"{root}/{slug}/{job}-{tag}"


def mirror_dir(slug: str, root: str = REMOTE_ROOT) -> str:
    """The slug's persistent copy on a host, refreshed incrementally by every
    job and never removed by one."""
    return f"{root}/{slug}/mirror"


def link_job_script(mirror: str, job: str) -> str:
    """Shell making the job directory from the mirror by hardlink (`cp -al`:
    a second for a project of any size, no bytes copied), falling back to
    rsync --link-dest where cp cannot. The engine writes new files into the
    job directory; nothing it does touches the mirror's inodes in place."""
    m, j = shlex.quote(mirror), shlex.quote(job)
    return (f"rm -rf {j} && {{ cp -al {m} {j} 2>/dev/null"
            f" || {{ rm -rf {j} && rsync -a --link-dest={m} {m}/ {j}/; }}; }}")


def output_dirs(cmd: list[str]) -> list[str]:
    """Parent directories of the files a command writes (`-o`, `--report`),
    relative ones only: the mirror leaves output trees such as qa/ behind, so
    a job creates them before the engine runs."""
    dirs: list[str] = []
    for n, token in enumerate(cmd[:-1]):
        if token in ("-o", "--report"):
            parent = str(Path(cmd[n + 1]).parent)
            if parent not in (".", "") and not parent.startswith("/") and parent not in dirs:
                dirs.append(parent)
    return dirs


def job_script(mirror: str, job: str, binary: str, rest: str, *, dirs: list[str] = ()) -> str:
    """The whole remote script: link the job dir from the mirror, enter it,
    make the output directories, run the engine."""
    made = "".join(f"mkdir -p {shlex.quote(d)} && " for d in dirs)
    return (f"{link_job_script(mirror, job)} && cd {shlex.quote(job)} && "
            f"{made}{binary} {rest}")


def prune_remote(hosts_: list[str], slug: str, *, verbose: bool = False) -> None:
    """Delete a slug's mirror on each host; the job directories are not touched."""
    for host in hosts_:
        remove_remote(host, mirror_dir(slug), verbose=verbose)


def sync_up_args(source: Path, host: str, remote: str, *, file: bool = False,
                 delete: bool = True, excludes: tuple[str, ...] = SYNC_EXCLUDES,
                 includes: tuple[str, ...] = ()) -> list[str]:
    """rsync argv shipping `source` (a directory, or one file when `file`) to host:remote.

    The remote parent is made in the same trip (`--rsync-path`), so a fresh
    job directory costs no extra ssh. Includes come before excludes, so a
    named file survives a pattern that would otherwise drop it.
    """
    parent = str(Path(remote).parent) if file else remote
    argv = ["rsync", "-az", "-e", SSH_COMMAND, f"--rsync-path=mkdir -p {shlex.quote(parent)} && rsync"]
    if delete:
        argv.append("--delete")
    argv += [f"--include={p}" for p in includes]
    argv += [f"--exclude={p}" for p in excludes]
    if file:
        argv += [str(source), f"{host}:{remote}"]
    else:
        argv += [f"{source}/", f"{host}:{remote}/"]
    return argv


def pull_back_args(host: str, remote: str, dest: Path,
                   patterns: tuple[str, ...] = PULL_BACK) -> list[str]:
    """rsync argv bringing a job's results (PULL_BACK) from host:remote to dest."""
    return (["rsync", "-az", "-e", SSH_COMMAND, "--include=*/"] + [f"--include={p}" for p in patterns]
            + ["--exclude=*", f"{host}:{remote}/", f"{dest}/"])


#: What a detached job leaves beside its directory: output, errors, exit status, pid.
JOB_FILES = (".out", ".err", ".status", ".pid")


def remove_remote(host: str, remote: str, *, verbose: bool = False) -> None:
    """Delete one job's directory on the host, and refuse anything that is not one."""
    path = remote.rstrip("/")
    if not any(path.startswith(root + "/") and len(Path(path).relative_to(root).parts) >= 2
               for root in SCRATCH_ROOTS):
        raise ValueError(f"refusing to remove {remote!r}: not a job directory under {SCRATCH_ROOTS}")
    parent = shlex.quote(str(Path(path).parent))
    control = " ".join(shlex.quote(path + ext) for ext in JOB_FILES)
    refuse_remote()
    proc = subprocess.run(ssh_argv(host, f"rm -rf {shlex.quote(path)}; rm -f {control}; "
                                         f"rmdir --ignore-fail-on-non-empty {parent} 2>/dev/null; true"),
                          capture_output=True, text=True)
    if verbose:
        note = "" if proc.returncode == 0 else f" (failed: {proc.stderr.strip()[:120]})"
        print(f"  removed {host}:{path}{note}", file=sys.stderr)


# Where a machine keeps the engine, in order: on PATH, a source checkout, a
# release tarball unpacked under ~/render (newest version last, so `tail`).
REMOTE_BIN_LOOKUP = ("$(command -v scene-render 2>/dev/null"
                     " || ls -x $HOME/src/scene-render/target/release/scene-render"
                     " $HOME/render/scene-render-*/scene-render $HOME/render/scene-render"
                     " /opt/scene-render/scene-render 2>/dev/null | tr ' ' '\\n' | sort -V | tail -1)")


@dataclass
class Host:
    """A render machine, as a probe found it."""
    addr: str
    engine: str = ""        # the binary's path there, "" when it has none
    version: str = ""
    ram_gb: int = 0
    cores: int = 0
    gpu: str = ""
    ffprobe: str = ""       # ffprobe there, which the delivery gate needs
    error: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.engine) and not self.error


_HOSTS: dict[str, Host] = {}


def host_names(args=None) -> list[str]:
    """Every configured render host, --host first when given."""
    if (args is not None and getattr(args, "local", False)) or os.environ.get(NO_REMOTE_ENV):
        return []
    names: list[str] = []
    for raw in ([getattr(args, "host", None) or ""] if args is not None else []) + [
            os.environ.get("RS_RENDER_HOSTS", ""), os.environ.get("RS_RENDER_HOST", "")]:
        for part in raw.split(","):
            part = part.strip()
            if part and part not in names:
                names.append(part)
    return names


def probe(addr: str, *, timeout: float = 12.0) -> Host:
    """One ssh round trip: engine, RAM, cores, GPU. Cached for the process;
    `ssh_alive` is what notices a host that went away afterwards."""
    if addr in _HOSTS:
        return _HOSTS[addr]
    binary_expr = os.environ.get("RS_RENDER_REMOTE_BIN") or REMOTE_BIN_LOOKUP
    script = (f"B={binary_expr}; if [ -x \"$B\" ]; then echo \"ENGINE=$B\"; "
              f"echo \"VERSION=$($B --version 2>/dev/null | head -1)\"; else echo ENGINE=; fi; "
              "echo \"RAM=$(free -g | awk 'NR==2{print $2}')\"; echo \"CORES=$(nproc)\"; "
              "echo \"GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)\"; "
              "echo \"FFPROBE=$(command -v ffprobe 2>/dev/null)\"")
    host = Host(addr=addr)
    refuse_remote()
    try:
        proc = subprocess.run(probe_argv(addr, script, int(timeout)),
                              capture_output=True, text=True, timeout=timeout + 20)
    except (subprocess.TimeoutExpired, OSError) as exc:
        host.error = str(exc)[:120]
        _HOSTS[addr] = host
        return host
    if proc.returncode != 0:
        host.error = (proc.stderr or proc.stdout).strip()[:160] or f"ssh exited {proc.returncode}"
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        value = value.strip()
        if key == "ENGINE":
            host.engine = value
        elif key == "VERSION":
            host.version = value
        elif key == "RAM":
            host.ram_gb = int(value or 0)
        elif key == "CORES":
            host.cores = int(value or 0)
        elif key == "GPU":
            host.gpu = value
        elif key == "FFPROBE":
            host.ffprobe = value
    _HOSTS[addr] = host
    return host


def hosts(args=None) -> list[Host]:
    return [probe(a) for a in host_names(args)]


def pick_host(candidates: list[Host], job: str) -> Host | None:
    """docs/compute.md's rule: memory, not GPU.

    `encode` and `audio` want the most RAM (a mix is assembled whole, and a
    long programme is RAM-bound); `still`, `frames`, `validate` and a short
    block want the least, which on this network is the faster GPU.
    """
    usable = [h for h in candidates if h.usable]
    if not usable:
        return None
    if job in ("encode", "audio"):
        return max(usable, key=lambda h: (h.ram_gb, h.cores))
    return min(usable, key=lambda h: (h.ram_gb, -h.cores))


#: addr -> (monotonic time, answered): one recheck serves the jobs of the next moments.
_ALIVE: dict[str, tuple[float, bool]] = {}
ALIVE_TTL = 20.0


def ssh_alive(addr: str, *, timeout: int = 4) -> bool:
    """Does the host answer ssh right now? One short round trip, remembered for
    ALIVE_TTL seconds; the process-wide probe cache never learns a host went away."""
    now = time.monotonic()
    seen = _ALIVE.get(addr)
    if seen and now - seen[0] < ALIVE_TTL:
        return seen[1]
    refuse_remote()
    try:
        ok = subprocess.run(probe_argv(addr, "true", timeout), capture_output=True, text=True,
                            timeout=timeout + 6).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        ok = False
    _ALIVE[addr] = (now, ok)
    return ok


def tcp_open(addr: str, *, port: int = 22, timeout: float = 3.0) -> bool:
    """Does the host accept a TCP connection on the ssh port? (`user@host` is read as host.)"""
    try:
        socket.create_connection((addr.rpartition("@")[2], port), timeout=timeout).close()
    except OSError:
        return False
    return True


def remote_host(args, job: str = "encode") -> str:
    """The ssh target to use for `job`, or "" to render here. Each candidate is
    rechecked with a short ssh first, so a host that stopped answering after
    this process probed it is passed over instead of hung on."""
    if args is not None and getattr(args, "local", False):
        return ""
    candidates = [h for h in hosts(args) if h.usable]
    while candidates:
        chosen = pick_host(candidates, job)
        if chosen is None:
            break
        if ssh_alive(chosen.addr):
            return chosen.addr
        candidates = [h for h in candidates if h is not chosen]
    names = host_names(args)
    return names[0] if names else ""


def _relativize(cmd: list[str], root: Path) -> list[str]:
    """Paths inside the project become relative, so they resolve in the copy."""
    out = []
    for token in cmd:
        try:
            rel = Path(token).resolve().relative_to(root.resolve())
            out.append(str(rel))
        except (ValueError, OSError):
            out.append(token)
    return out


def run_job(cmd: list[str], project_root: Path, host: str, *, job: str,
            verbose: bool = False, remote: str | None = None,
            keep_remote: bool = False) -> subprocess.CompletedProcess:
    """Run one engine command for `project_root` on `host`: the hook for any
    stage (the vfx stage's encode windows, for one).

    `cmd` is the argv as it would run here (`scene-render ...`; the binary
    is resolved on the host, paths inside the project become relative). The
    slug's mirror is refreshed incrementally, the job directory is hardlinked
    from it in a second, the engine runs there, the results (PULL_BACK) come
    back into `project_root`, and the job directory is removed. `job` names
    the directory (`still`, `encode`, `window`...); `remote` fixes it when
    the caller needs it afterwards (to localise a report); `keep_remote`
    leaves it for debugging. The mirror stays until --prune-remote.
    """
    return _run_remote(cmd, project_root, host, verbose=verbose, job=job,
                       remote=remote, keep_remote=keep_remote)


#: How long a dropped session is chased: REATTACH_TRIES polls, REATTACH_WAIT s apart.
REATTACH_TRIES = 120
REATTACH_WAIT = 15.0
#: Backstop for one attach: seconds before the loop gives up on a job that is neither
#: finished nor provably dead.
JOB_DEADLINE = 6 * 3600
#: Lines the attach script prints, so the job's own exit code (255 included) is never
#: confused with a dropped ssh session, and a vanished job is told apart.
EXIT_MARK = "__YT_JOB_EXIT__="
LOST_MARK = "__YT_JOB_LOST__"


def detached_script(script: str, remote: str) -> str:
    """Shell that starts `script` on its own session (it outlives the ssh that
    started it), recording its pid, output and exit code beside the job directory,
    then waits for the result like an attached run."""
    out, err, status, pid = (shlex.quote(remote + ext) for ext in JOB_FILES)
    body = (f"echo $$ > {pid}; ( {script} ) > {out} 2> {err}; "
            f"echo $? > {status}.tmp; mv {status}.tmp {status}")
    return (f"rm -f {out} {err} {status} {pid}; "
            f"nohup setsid sh -c {shlex.quote(body)} > /dev/null 2>&1 < /dev/null & "
            f"{attach_script(remote)}")


def attach_script(remote: str, deadline: int = JOB_DEADLINE) -> str:
    """Shell that waits for a detached job's status file, replays its output, then
    prints the job's code as a marker line and exits 0 (ssh's own 255 stays reserved
    for a dropped session). If the status file is absent and the recorded pid is no
    longer alive, or the deadline passes, it prints a lost marker instead. It never
    starts anything."""
    out, err, status, pid = (shlex.quote(remote + ext) for ext in JOB_FILES)
    return (f"end=$(( $(date +%s) + {deadline} )); "
            f"while [ ! -f {status} ]; do "
            f"if [ -f {pid} ] && ! kill -0 $(cat {pid}) 2>/dev/null && [ ! -f {status} ]; "
            f"then echo {LOST_MARK}=dead; exit 0; fi; "
            f"if [ $(date +%s) -ge $end ]; then echo {LOST_MARK}=deadline; exit 0; fi; "
            f"sleep 2; done; "
            f"cat {out}; cat {err} >&2; "
            f"printf '\\n{EXIT_MARK}%s\\n' \"$(cat {status})\"; exit 0")


def _read_job(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess | None:
    """The job's own result from an attach's output, or None when the attach printed
    no final marker. A lost job becomes a non-zero result with a message."""
    text = proc.stdout or ""
    lost = re.search(rf"(?m)^{LOST_MARK}=(\w+)\n?\Z", text)
    if lost:
        why = ("its process is gone and it wrote no status (host rebooted or engine killed)"
               if lost.group(1) == "dead" else f"it did not finish within {JOB_DEADLINE} s")
        return subprocess.CompletedProcess(proc.args, 1, text[:lost.start()],
                                           (proc.stderr or "") + f"remote job lost: {why}\n")
    done = re.search(rf"\n?{EXIT_MARK}(\d+)\n?\Z", text)
    if done:
        return subprocess.CompletedProcess(proc.args, int(done.group(1)), text[:done.start()],
                                           proc.stderr)
    return None


def _run_detached(host: str, script: str, remote: str, *, verbose: bool = False
                  ) -> tuple[subprocess.CompletedProcess, bool]:
    """Run `script` on the host detached; return (result, settled). Only a marker is
    authoritative: whatever ssh's exit code (255, 143, -15, 1...), output without an
    EXIT or LOST marker means the job's outcome is unknown, so poll (attach only)
    until it reports. settled is False when the polls ran out with no marker: the
    job may still be running and its directory must be left alone."""
    refuse_remote()
    proc = subprocess.run(ssh_argv(host, detached_script(script, remote)),
                          capture_output=True, text=True)
    tries = 0
    while True:
        job = _read_job(proc)
        if job is not None:
            return job, True
        if tries >= REATTACH_TRIES:
            break
        tries += 1
        if verbose:
            print(f"  no result marker from {host} (ssh exit {proc.returncode}); "
                  f"job may still run there, polling ({tries})", file=sys.stderr)
        time.sleep(REATTACH_WAIT)
        refuse_remote()
        proc = subprocess.run(ssh_argv(host, attach_script(remote)), capture_output=True, text=True)
    note = (f"remote job outcome unknown: no result marker from {host} after {tries} polls; "
            f"the job may still be running. Left in place: {host}:{remote} "
            f"(pull it later from there)\n")
    if verbose:
        print(f"  {note.strip()}", file=sys.stderr)
    return subprocess.CompletedProcess(proc.args, proc.returncode or 1, proc.stdout,
                                       (proc.stderr or "") + note), False


def _run_remote(cmd: list[str], project_root: Path, host: str, *, verbose: bool,
                job: str = "encode", remote: str | None = None,
                keep_remote: bool = False) -> subprocess.CompletedProcess:
    """run_job's body: mirror, link, run, pull back, remove."""
    remote = remote or remote_dir(project_root.name, job, job_tag())
    mirror = mirror_dir(project_root.name)
    if verbose:
        print(f"  sync -> {host}:{mirror}", file=sys.stderr)
    refuse_remote()
    subprocess.run(sync_up_args(project_root, host, mirror), check=True,
                   capture_output=True, text=True)
    settled = False
    try:
        known = _HOSTS.get(host)
        binary = (shlex.quote(known.engine) if known and known.engine else
                  os.environ.get("RS_RENDER_REMOTE_BIN") or REMOTE_BIN_LOOKUP)
        relative = _relativize(cmd, project_root)
        rest = " ".join(shlex.quote(t) for t in relative[1:])
        script = job_script(mirror, remote, binary, rest, dirs=output_dirs(relative))
        if verbose:
            print(f"  ssh {host} {script}", file=sys.stderr)
        proc, settled = _run_detached(host, script, remote, verbose=verbose)
        if not settled:
            return proc                 # outcome unknown: no pull-back, no removal
        refuse_remote()
        pulled = subprocess.run(pull_back_args(host, remote, project_root),
                                capture_output=True, text=True)
        if pulled.returncode and verbose:
            print(f"  warning: could not pull results back: {pulled.stderr.strip()[:200]}",
                  file=sys.stderr)
    finally:
        if not settled:
            pass                        # the job may still run there: leave its directory
        elif keep_remote:
            if verbose:
                print(f"  kept {host}:{remote}", file=sys.stderr)
        else:
            remove_remote(host, remote, verbose=verbose)
    return proc


def engine_validate(xml_bytes: bytes, *, host: str = "", timeout: float = 120.0
                    ) -> list[str] | None:
    """The engine's own validation of a document, as finding strings.

    Level 3 of the SDK's ladder: the schema and the SDK's checks let a
    document through that the engine refuses (a path with no box, for one),
    so a document is not trusted until the engine has read it. Runs
    sr_core.scene.engine.level3 against a local binary when there is one,
    else ships the bytes to RS_RENDER_HOST and runs `validate` there. None
    means no engine could be asked; the caller decides what that is worth.
    """
    binary = engine()
    if binary is not None:
        try:
            from sr_core.scene.engine import level3
            return [str(f) for f in level3(xml_bytes, binary)]
        except Exception as exc:  # the SDK's own EngineError, or no sr_core
            return [f"error: sdk:engine-failed: {exc}"]
    host = host or remote_host(None, "validate")
    if not host:
        return None
    remote = f"{REMOTE_ROOT}/validate-{os.getpid()}.scene.xml"
    binary_expr = os.environ.get("RS_RENDER_REMOTE_BIN") or (
        REMOTE_BIN_LOOKUP)
    script = (f"mkdir -p {REMOTE_ROOT} && cat > {shlex.quote(remote)} && "
              f"{binary_expr} validate --format json --no-assets {shlex.quote(remote)}; "
              f"code=$?; rm -f {shlex.quote(remote)}; exit $code")
    refuse_remote()
    try:
        proc = subprocess.run(probe_argv(host, script, 8), input=xml_bytes, capture_output=True,
                              timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"error: sdk:engine-failed: {exc}"]
    out = proc.stdout.decode("utf-8", "replace")
    try:
        report = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        text = (proc.stderr.decode("utf-8", "replace") or out).strip()
        return [] if proc.returncode == 0 else [f"error: {text[-400:]}"]
    findings = []
    for file in (report.get("result") or report).get("files", []) or []:
        for diag in file.get("diagnostics") or []:
            sev = str(diag.get("severity", "error")).lower()
            code = diag.get("code") or ""
            at = diag.get("id") or diag.get("node") or ""
            msg = diag.get("message") or str(diag)
            findings.append(f"{sev}[{code}] ({at}): {msg}" if at else f"{sev}[{code}]: {msg}")
    return findings


def engine_render_still(xml_bytes: bytes, at: float, *, host: str = "",
                        quality: str = "draft", files: dict[str, bytes] | None = None,
                        doc_path: str = "doc.scene.xml", timeout: float = 600.0
                        ) -> tuple[bytes | None, str]:
    """One frame of a document, rendered by the engine: (png, message).

    Validation cannot see a resource limit, a camera that frames nothing or a
    solver that refuses its grid; a frame can. This is the gate an authored
    document has to pass before it counts as authored. `files` are shipped
    with the document, keyed by path relative to the working directory the
    engine runs in (the document itself is `doc_path`); a symbol document
    normally needs none. The message carries the engine's diagnostics, one
    per line after the first, so a caller can show them to an author.
    """
    host = host or remote_host(None, "still")
    binary = engine()
    bundle: dict[str, bytes] = dict(files or {})
    bundle[doc_path] = xml_bytes
    tag = f"still-{os.getpid()}-{uuid.uuid4().hex[:10]}"     # parallel stills must not share it
    if binary is None and not host:
        return None, "no engine reachable"
    if binary is not None and not host:
        work = Path(REMOTE_ROOT) / tag
        for rel, data in bundle.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(data)
        proc = subprocess.run([str(binary), "render", doc_path, "-t", str(at), "-o", "frame.png",
                               "--json"], cwd=work, capture_output=True, text=True, timeout=timeout)
        png = (work / "frame.png").read_bytes() if (work / "frame.png").is_file() else None
        shutil.rmtree(work, ignore_errors=True)
        return png, _still_message(proc.returncode, proc.stdout, proc.stderr, "rendered locally")
    known = _HOSTS.get(host) or probe(host)
    binary_expr = shlex.quote(known.engine) if known.engine else REMOTE_BIN_LOOKUP
    remote = f"{REMOTE_ROOT}/{tag}"
    # The bundle travels as a tar on stdin; the report comes back on stderr
    # (JSON first, then the engine's messages) and the PNG on stdout.
    script = (f"mkdir -p {remote} && cd {remote} && tar -xf - && "
              # the render dies on the host by the deadline this side keeps: a
              # still this side gave up on must not keep the GPU for hours
              f"timeout -k 10 {max(1, int(timeout) - 5)} "
              f"{binary_expr} render {shlex.quote(doc_path)} -t {at} -o frame.png --json "
              f"> report.json 2> messages.txt; code=$?; cat report.json >&2; echo >&2; "
              f"cat messages.txt >&2; [ -f frame.png ] && cat frame.png; cd /; rm -rf {remote}; exit $code")
    refuse_remote()
    try:
        proc = subprocess.run(probe_argv(host, script, 8),
                              input=_tar(bundle), capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"render host unreachable: {exc}"
    err = proc.stderr.decode("utf-8", "replace")
    report, _, messages = err.partition("\n")
    png = proc.stdout if proc.stdout[:8] == b"\x89PNG\r\n\x1a\n" else None
    return png, _still_message(proc.returncode, report, messages, f"rendered on {host}")


def _tar(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, data in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _still_message(code: int, report: str, messages: str, ok_text: str) -> str:
    """The engine's render report, flattened to lines an author can read."""
    lines: list[str] = []
    try:
        rep = json.loads(report) if report.strip() else {}
    except json.JSONDecodeError:
        rep = {}
    for d in rep.get("diagnostics") or []:
        lines.append(f"{d.get('severity', 'warning')}[{d.get('code', '?')}]: {d.get('message', '')}")
    result = rep.get("result") or {}
    warnings = result.get("warnings")
    for d in (warnings if isinstance(warnings, list) else []):
        if isinstance(d, str):
            lines.append(f"warning: {d}")
    for d in result.get("findings") or []:
        if isinstance(d, dict):
            lines.append(f"{d.get('severity', 'warning')}: {d.get('message', '')}")
    unsupported = result.get("unsupported")
    for d in (unsupported if isinstance(unsupported, list) else []):
        lines.append(f"warning: unsupported: {d}")
    if rep.get("error"):
        lines.insert(0, f"error: {rep['error'] if isinstance(rep['error'], str) else json.dumps(rep['error'])[:300]}")
    if code != 0 and not any(l.startswith("error") for l in lines):
        tail = messages.strip().splitlines()[-3:] if messages.strip() else []
        lines.insert(0, "error: the engine could not render the frame" +
                     (": " + " | ".join(t.strip()[:160] for t in tail) if tail else f" (exit {code})"))
    head = ok_text if code == 0 else "render failed"
    return "\n".join([head] + lines)


def _slice_ranges(start: float, end: float, slices: int) -> list[tuple[float, float]]:
    step = (end - start) / slices
    return [(round(start + i * step, 3), round(end if i == slices - 1 else start + (i + 1) * step, 3))
            for i in range(slices)]


def encode_sliced(scene: str, project_root: Path, *, slices: int, quality: str | None,
                  start: float, end: float, strict: bool, verbose: bool) -> int:
    """A long encode across every usable host: video slices, one audio mix, join.

    Each slice is its own --start/--end encode (the only split that crosses a
    machine); the audio mix is rendered once on the host with the most RAM,
    because the engine assembles it whole whatever range is asked for; the
    slices are joined by stream copy and the mix muxed in, on that same host.
    """
    usable = [h for h in hosts() if h.usable]
    if not usable:
        print("error: no host with the engine; set RS_RENDER_HOSTS", file=sys.stderr)
        return 1
    big = pick_host(usable, "audio")
    ranges = _slice_ranges(start, end, slices)
    if verbose:
        print(f"  {slices} slice(s) over {', '.join(h.addr for h in usable)}; audio mix on {big.addr}",
              file=sys.stderr)
    rel_scene = str(Path(scene).resolve().relative_to(project_root.resolve()))

    def one(i: int, rng: tuple[float, float]) -> tuple[int, int, str]:
        host = usable[i % len(usable)]
        out = f"slice-{i:02d}.mp4"
        cmd = ["scene-render", "encode", rel_scene, "-o", out, "--no-upload",
               "--start", str(rng[0]), "--end", str(rng[1])]
        if quality:
            cmd += ["--quality", quality]
        if strict:
            cmd.append("--strict")
        proc = _run_remote(cmd, project_root, host.addr, verbose=verbose)
        return i, proc.returncode, (proc.stderr or proc.stdout).strip()[-200:]

    with ThreadPoolExecutor(max_workers=len(usable)) as pool:
        results = list(pool.map(lambda ir: one(*ir), enumerate(ranges)))
    failed = [r for r in results if r[1] != 0]
    for i, code, tail in failed:
        print(f"  slice {i} failed ({code}): {tail}", file=sys.stderr)
    if failed:
        return 1

    # The mix runs in a directory that is kept until the join: every slice is
    # in that project copy, pushed again by the audio run's sync after the
    # pull-backs above, and the join happens there.
    remote = remote_dir(project_root.name, "audio", job_tag())
    mix = _run_remote(["scene-render", "encode", rel_scene, "-o", "mix.wav", "--no-upload",
                       "--start", str(start), "--end", str(end)], project_root, big.addr,
                      verbose=verbose, remote=remote, keep_remote=True)
    try:
        if mix.returncode != 0:
            print(f"  audio mix failed: {(mix.stderr or mix.stdout).strip()[-200:]}", file=sys.stderr)
            return 1
        join = (f"cd {shlex.quote(remote)} && "
                "printf 'file slice-%02d.mp4\n' " + " ".join(str(i) for i in range(slices)) + " > slices.txt && "
                "ffmpeg -y -loglevel error -f concat -safe 0 -i slices.txt -c copy joined.mp4 && "
                "ffmpeg -y -loglevel error -i joined.mp4 -i mix.wav -map 0:v -map 1:a -c:v copy "
                "-c:a aac -b:a 192k -shortest video.mp4 && rm -f joined.mp4 && ls -la video.mp4")
        refuse_remote()
        proc = subprocess.run(ssh_argv(big.addr, join), capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  join failed on {big.addr}: {proc.stderr.strip()[-300:]}", file=sys.stderr)
            return 1
        pull = ["rsync", "-az", "-e", SSH_COMMAND, f"{big.addr}:{remote}/video.mp4", f"{project_root}/video.mp4"]
        refuse_remote()
        subprocess.run(pull, capture_output=True, text=True)
    finally:
        remove_remote(big.addr, remote, verbose=verbose)
    print(f"wrote {project_root / 'video.mp4'}  (joined from {slices} slices; "
          f"audio mixed once on {big.addr})", file=sys.stderr)
    return 0


def estimate(scene: str, project_root: Path, *, duration: float, shots: list[dict],
             windows: int = 6, quality: str | None = "draft", host: str = "",
             verbose: bool = True) -> dict:
    """What a full encode will cost, from one-second windows across the programme.

    A document can validate, render its frames and still take an hour: a
    solver is evaluated per frame, serially, and only an encode shows it. So
    before a long encode, `windows` one-second slices spread over the
    duration are encoded on the stills host, each window is charged to the
    shot playing then, and the total is projected. The result names the
    costly shots so the author can trade the construct for a cheaper one
    before anyone waits for the full render.
    """
    host = host or remote_host(None, "still")
    rel_scene = str(Path(scene).resolve().relative_to(project_root.resolve()))
    if windows < 1 or duration <= 0:
        return {"windows": [], "per_second": 0.0, "projected_seconds": 0, "host": host, "hot": []}
    starts = window_starts(duration, windows)

    def shot_at(t: float) -> dict | None:
        for sh in shots:
            if sh.get("at", 0) <= t < sh.get("at", 0) + sh.get("seconds", 0):
                return sh
        return None

    rows: list[dict] = []
    for i, t in enumerate(starts):
        cmd = ["scene-render", "encode", rel_scene, "-o", f".estimate-{i}.mp4", "--no-upload",
               "--json", "--report", f".estimate-{i}.json", "--start", str(t), "--end", str(round(t + 1, 2))]
        if quality:
            cmd += ["--quality", quality]
        if host:
            proc = _run_remote(cmd, project_root, host, verbose=False, job="estimate")
        else:
            proc = subprocess.run([str(engine())] + _relativize(cmd, project_root)[1:],
                                  cwd=project_root, capture_output=True, text=True)
        report_path = project_root / f".estimate-{i}.json"
        rep_ = read_json(report_path, warn=False) if report_path.is_file() else None
        out = ((rep_ or {}).get("result") or {}).get("outputs") or [{}]
        seconds = float(out[0].get("seconds") or 0.0) if proc.returncode == 0 else None
        stages = out[0].get("stage_seconds") or []
        sh = shot_at(t)
        rows.append({"at": t, "seconds": seconds, "stages": [round(x, 1) for x in stages],
                     "shot": (sh or {}).get("label", "?"), "section": (sh or {}).get("section", ""),
                     "error": None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()[-160:]})
        for junk in (report_path, project_root / f".estimate-{i}.mp4"):
            junk.unlink(missing_ok=True)
        if verbose:
            cost = f"{seconds:6.1f}s" if seconds is not None else " failed"
            print(f"  {t:6.1f}s  shot {rows[-1]['shot']:<5} {cost} per second of video"
                  f"  stages {rows[-1]['stages']}", file=sys.stderr)
    return project_cost(rows, duration, host)


def window_starts(duration: float, windows: int) -> list[float]:
    """One-second windows centred in equal slices of the programme."""
    step = duration / windows
    return [round(max(0.0, min(i * step + step / 2 - 0.5, max(duration - 1, 0))), 2)
            for i in range(windows)]


def project_cost(rows: list[dict], duration: float, host: str = "") -> dict:
    """Average the windows' engine seconds and name the ones that dominate."""
    good = [r for r in rows if r.get("seconds") is not None]
    per_second = sum(r["seconds"] for r in good) / len(good) if good else 0.0
    hot = [r for r in sorted(good, key=lambda r: -r["seconds"])[:3]
           if r["seconds"] > max(2 * per_second, 10)]
    return {"windows": rows, "per_second": round(per_second, 2),
            "projected_seconds": round(per_second * duration), "host": host,
            "hot": [{"shot": r["shot"], "section": r["section"], "at": r["at"],
                     "seconds": r["seconds"]} for r in hot]}


def loudness_of(output: dict) -> dict:
    """An output's loudness as {integrated, true_peak, range, short_term_max}.

    The engine reports the numbers flat on the output (`loudness` is the
    integrated LUFS, beside `true_peak`, `loudness_range` and
    `short_term_max`); an older shape nested them under `loudness`. A silent
    output reports None for each, and gets an empty dict.
    """
    loud = output.get("loudness")
    if isinstance(loud, dict):
        return {k: v for k, v in loud.items() if v is not None}
    pairs = (("integrated", loud), ("true_peak", output.get("true_peak")),
             ("range", output.get("loudness_range")),
             ("short_term_max", output.get("short_term_max")))
    return {k: v for k, v in pairs if isinstance(v, (int, float))}


def summarise(report: dict) -> list[str]:
    """The lines worth reading out of an encode report: each output with
    its loudness, then every finding and engine warning, each once."""
    lines = []
    for output in (report.get("result") or {}).get("outputs") or []:
        files = output.get("files") or []
        name = ", ".join(str(f) for f in files) or output.get("id", "?")
        bits = [name]
        if output.get("fps"):
            bits.append(f"{output['fps']} fps")
        loud = loudness_of(output)
        if "integrated" in loud:
            bits.append(f"{loud['integrated']:.1f} LUFS")
        if "true_peak" in loud:
            bits.append(f"peak {loud['true_peak']:.1f} dBTP")
        if "range" in loud:
            bits.append(f"range {loud['range']:.1f} LU")
        lines.append(" · ".join(str(b) for b in bits))
        seen: set[str] = set()

        def say(severity: str, message: str) -> None:
            key = message.strip()
            if key and key not in seen:
                seen.add(key)
                lines.append(f"  [{severity}] {message}")

        for finding in output.get("findings") or []:
            if isinstance(finding, dict):
                say(str(finding.get("severity", "note")), str(finding.get("message", finding)))
            else:
                say("note", str(finding))
        for warning in output.get("evaluation_warnings") or []:
            if isinstance(warning, dict):
                code = warning.get("code")
                say("warning", (f"{code}: " if code else "") + str(warning.get("message", "")))
            else:
                say("warning", str(warning))
        for warning in output.get("warnings") or []:
            text = str(warning)
            # "CODE: message" repeats a finding already said by its message.
            body = text.split(": ", 1)[1] if ": " in text else text
            if body.strip() not in seen:
                say("warning", text)
    return lines


def register(sub, parents=()) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "render", parents=list(parents),
        help="render or encode the built scene with the engine",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output", metavar="ID",
                   help="render only this <output> of the document")
    p.add_argument("--scene", metavar="FILE",
                   help="the document (default: the project's scene.xml)")
    p.add_argument("--out", dest="video_out", metavar="FILE",
                   help="where to write (default: the project's video.mp4)")
    p.add_argument("--quality", choices=["draft", "preview", "final"],
                   help="fidelity tier; draft and preview are for iterating")
    p.add_argument("--start", type=float, help="first second to render")
    p.add_argument("--end", type=float, help="last second to render")
    p.add_argument("--audio-only", action="store_true", dest="audio_only",
                   help="encode just the mix to WAV: the cheapest way to find "
                        "out whether the audio graph fits in memory, since the "
                        "mix is built whole and costs tracks x duration. A "
                        "--start/--end range does NOT make it cheaper -- the "
                        "whole mix is assembled either way (measured) -- so "
                        "run it once, on the machine with the most RAM")
    p.add_argument("--still", type=float, metavar="TIME",
                   help="render one frame at this second instead of encoding")
    p.add_argument("--frames", metavar="A..B", help="render this frame range")
    p.add_argument("--strict", action="store_true",
                   help="fail when the engine reports any warning or error")
    p.add_argument("--parallel", help="segments to encode at once (auto or N)")
    p.add_argument("--validate", action="store_true",
                   help="only run the engine's validation over the document")
    p.add_argument("--host", metavar="USER@HOST",
                   help="render on this machine over ssh (default: route among $RS_RENDER_HOSTS)")
    p.add_argument("--estimate", type=int, nargs="?", const=6, default=0, metavar="N",
                   help="encode N one-second windows (default 6) and project the full cost "
                        "per shot instead of rendering")
    p.add_argument("--slices", type=int, default=1,
                   help="split a full encode across the render hosts (default 1: no split)")
    p.add_argument("--local", action="store_true",
                   help="render here even when render hosts are configured")
    p.add_argument("--keep-remote", action="store_true", dest="keep_remote",
                   help="leave the job's directory on the host for debugging "
                        "(default: removed once the results are back)")
    p.add_argument("--prune-remote", action="store_true", dest="prune_remote",
                   help="delete this project's mirror on every render host and stop "
                        "(the next job uploads it again in full)")
    p.set_defaults(run=run)
    return p


#: Flags from the shared parents (quiet, model, work...) that run() reads.
PARENT_DEFAULTS = {"quiet": False, "model": None, "title": None, "project": None,
                   "work": None}


def defaults() -> dict:
    """Every flag `yt render` registers, with its default -- from the parser
    itself, so a flag added later is covered without anyone remembering."""
    parser = register(argparse.ArgumentParser(add_help=False).add_subparsers())
    found = {a.dest: a.default for a in parser._actions
             if a.dest != "help" and a.default is not argparse.SUPPRESS}
    return PARENT_DEFAULTS | found


def with_defaults(args) -> argparse.Namespace:
    """Other stages (qa, pilot) call run() with a partial Namespace: give every
    flag that is missing, or None, the default the CLI would have set."""
    for name, default in defaults().items():
        if getattr(args, name, None) is None:
            setattr(args, name, default)
    return args


def report_path_for(render_json: Path, job: str, output: str | None) -> Path:
    """Only the film's own encode owns render.json: a still, a validation, an
    audio pass or a named output (a pilot) writes render-<job>.json, so the
    report the delivery gate reads for video.mp4 is never overwritten."""
    if job == "encode" and not output:
        return render_json
    return render_json.with_name(f"render-{output or job}.json")


def _localise(node, remote: str, local: str):
    """Rewrite every path string under the remote project copy to the local one."""
    if isinstance(node, dict):
        return {k: _localise(v, remote, local) for k, v in node.items()}
    if isinstance(node, list):
        return [_localise(v, remote, local) for v in node]
    if isinstance(node, str) and node.startswith(remote + "/"):
        return local + node[len(remote):]
    return node


def run(args) -> int:
    with_defaults(args)
    idea, project = workspace.resolve(args, inputs=("scene",))
    scene = project.source("scene", args.scene)
    if not scene:
        print("error: no scene in this project; run `yt build` first",
              file=sys.stderr)
        return 1
    if args.prune_remote:
        names = host_names(args)
        if not names:
            print("error: no render hosts configured; nothing to prune", file=sys.stderr)
            return 1
        prune_remote(names, project.root.name, verbose=not args.quiet)
        return 0
    job = ("validate" if args.validate else "audio" if getattr(args, "audio_only", False)
           else "still" if (args.still is not None or args.frames) else "encode")
    host = remote_host(args, job)
    binary = engine()
    if binary is None and not host:
        print(f"error: {BUILD_HINT} Or set RS_RENDER_HOSTS to machines that have it.",
              file=sys.stderr)
        return 1
    if host:
        binary = Path("scene-render")      # resolved on the host, see _run_remote
    if not args.quiet:
        known = _HOSTS.get(host)
        where = (f"{host} ({known.version or 'remote'}, {known.ram_gb} GB, {known.gpu or 'no gpu'})"
                 if host and known else (host or str(binary)))
        print(f"engine: {where} for {job}", file=sys.stderr)
    if getattr(args, "estimate", False):
        build = read_json(project.source("build"), warn=False) or {}
        duration = args.end if args.end is not None else float((build.get("scene") or {}).get("duration") or 0)
        if not duration:
            print("error: --estimate needs the scene's duration; run `yt build` first or pass --end",
                  file=sys.stderr)
            return 1
        est = estimate(scene, project.root, duration=duration, shots=build.get("shots", []),
                       windows=args.estimate, quality=args.quality or "draft",
                       host=remote_host(args, "still"), verbose=not args.quiet)
        mins = est["projected_seconds"] / 60
        print(f"\nprojected: {mins:.0f} min for {duration:.0f}s at {args.quality or 'draft'} "
              f"({est['per_second']:.1f}s per second of video, {len(est['windows'])} windows on "
              f"{est['host'] or 'this machine'})", file=sys.stderr)
        for h in est["hot"]:
            print(f"  hot: shot {h['shot']} ({h['section']}) costs {h['seconds']:.0f}s per second "
                  "-- a solver or path tracer evaluated per frame; trade it for a cheaper "
                  "construct or accept the wait", file=sys.stderr)
        write_json(project.root / "estimate.json", est)
        return 0
    if args.slices > 1 and job == "encode" and host:
        duration = (read_json(project.source("build"), warn=False) or {}).get("scene", {}).get("duration")
        end = args.end if args.end is not None else float(duration or 0)
        if not end:
            print("error: --slices needs the scene's duration; run `yt build` first or pass --end",
                  file=sys.stderr)
            return 1
        return encode_sliced(scene, project.root, slices=args.slices, quality=args.quality,
                             start=args.start or 0.0, end=end, strict=args.strict,
                             verbose=not args.quiet)

    report_path = report_path_for(Path(project.out("render")), job, args.output)
    if args.validate:
        cmd = [str(binary), "validate", scene]
    elif getattr(args, "audio_only", False):
        target = args.video_out or str(project.root / "mix.wav")
        cmd = [str(binary), "encode", scene, "-o", target, "--json",
               "--report", str(report_path), "--no-upload"]
        for flag, value in (("--start", args.start), ("--end", args.end)):
            if value is not None:
                cmd += [flag, str(value)]
    elif args.still is not None or args.frames:
        target = args.video_out or str(project.root / "still.png")
        cmd = [str(binary), "render", scene, "-o", target]
        cmd += (["--frames", args.frames] if args.frames
                else ["--time", str(args.still)])
        if args.quality:
            cmd += ["--quality", args.quality]
        if args.strict:
            cmd.append("--strict")
    else:
        target = args.video_out or project.out("video")
        wanted = getattr(args, "output", None)
        cmd = [str(binary), "encode", scene, "--json",
               "--report", str(report_path), "--no-upload"]
        # Naming an output renders that one as the document declares it; -o
        # would replace every output with one ad-hoc file instead.
        cmd += ["--output", wanted] if wanted else ["-o", target]
        for flag, value in (("--quality", args.quality), ("--start", args.start),
                            ("--end", args.end), ("--parallel", args.parallel)):
            if value is not None:
                cmd += [flag, str(value)]
        if args.strict:
            cmd.append("--strict")

    if not args.quiet:
        print("  " + " ".join(cmd), file=sys.stderr)
    remote = remote_dir(project.root.name, job, job_tag()) if host else ""
    if host:
        try:
            proc = _run_remote(cmd, project.root, host, verbose=not args.quiet, job=job,
                               remote=remote, keep_remote=args.keep_remote)
        except subprocess.CalledProcessError as exc:
            print(f"error: could not reach {host}: {exc.stderr.strip()[:300]}",
                  file=sys.stderr)
            return 1
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stderr.strip() and not args.quiet:
        print(proc.stderr.strip()[-4000:], file=sys.stderr)

    report = {}
    if proc.stdout.strip().startswith("{"):
        try:
            report = json.loads(proc.stdout)
            if host:
                # The engine ran inside the remote copy, so its report names
                # files under the job's directory; the delivery gate matches
                # them against the project's own files, so localise them.
                report = _localise(report, remote, str(project.root.resolve()))
            report_path.write_text(json.dumps(report, indent=2,
                                              ensure_ascii=False) + "\n",
                                   encoding="utf-8")
            print(f"wrote {report_path}", file=sys.stderr)
        except json.JSONDecodeError:
            print(proc.stdout.strip()[-2000:])
    elif proc.stdout.strip():
        print(proc.stdout.strip()[-4000:])

    if report:
        print(f"\n{idea.title} — RENDER")
        for line in summarise(report):
            print(f"  {line}")

    if proc.returncode != 0:
        print(f"\nengine exited {proc.returncode}", file=sys.stderr)
    return proc.returncode
