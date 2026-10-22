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

The engine is Rust and is not bundled. It is found through RS_RENDER_BIN (the
binary, or a directory that holds it), on PATH as `scene-render`, in a checkout
beside this one (`cargo build --release`), or in a release archive from GitHub
Releases unpacked beside this one (`scene-render-vX-linux-x86_64/scene-render`,
the newest version when there are several; see LOCAL_BIN_GLOBS) -- or it runs on
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
import hashlib
import json
import math
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

from . import flags, workspace
from .core import read_json, write_json

BUILD_HINT = ("the scene-render binary was not found. Set RS_RENDER_BIN to it, "
              "put `scene-render` on PATH, build the checkout "
              "(cd ../scene-render && cargo build --release), or unpack a release "
              "archive beside this checkout (scene-render-vX-linux-x86_64/scene-render)")

#: Where the engine may sit under each of local_roots(), nearest root first and,
#: within one root, in this order: a developer's own build wins over an archive.
#: A pattern with `*` takes the newest version among its own matches, ordered as
#: `sort -V` orders them. (REMOTE_BIN_LOOKUP differs: on a host, `sort -V` runs
#: over every candidate at once, a cargo build included.) Home and /opt are not
#: searched: a binary left there would quietly pull work off the render hosts
#: (SKILL.md invariant 9); name one with RS_RENDER_BIN instead.
LOCAL_BIN_GLOBS = (
    "scene-render/target/release/scene-render",       # cargo build --release in the checkout
    "scene-render/scene-render",                      # a release binary dropped into the checkout
    "scene-render/dist/scene-render-*/scene-render",  # the release workflow's own dist/
    "scene-render-*/scene-render",                    # an unpacked release archive
    "scene-render-*/scene-render-*/scene-render",     # ... inside a folder of archives
)


def local_roots() -> tuple[Path, ...]:
    """The directories LOCAL_BIN_GLOBS are tried under: beside this checkout, then
    the user's src/."""
    here = Path(__file__).resolve().parent.parent
    return (here.parent, Path.home() / "src")


def _version_key(path: Path) -> tuple:
    """Order paths like `sort -V`: every run of digits in every component as an
    int, so scene-render-v0.10.0 sorts after scene-render-v0.7.1."""
    return tuple(tuple(int(n) for n in re.findall(r"\d+", part)) for part in path.parts)


def _binary_under(directory: Path) -> Path | None:
    """The engine inside a directory RS_RENDER_BIN names: the binary itself, or the
    one unpacked archive (or newest of several) the directory holds."""
    direct = directory / "scene-render"
    if direct.is_file():
        return direct
    found = [p for p in directory.glob("scene-render-*/scene-render") if p.is_file()]
    return max(found, key=_version_key) if found else None


def engine() -> Path | None:
    """The engine binary, wherever this machine keeps it."""
    env = os.environ.get("RS_RENDER_BIN")
    if env:
        named = Path(env)
        if named.is_file():
            return named
        if named.is_dir():
            inside = _binary_under(named)
            if inside:
                return inside
    found = shutil.which("scene-render")
    if found:
        return Path(found)
    for root in local_roots():
        for pattern in LOCAL_BIN_GLOBS:
            if "*" not in pattern:
                binary = root / pattern
                if binary.is_file():
                    return binary
                continue
            matches = [p for p in root.glob(pattern) if p.is_file()]
            if matches:
                return max(matches, key=_version_key)
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
                 "decisions/", "video*.mp4", "pilot*.mp4", "/mix.wav", ".slices/",
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


#: The line a sampled job prints on stderr: the peak GPU memory (MiB) it saw.
GPU_MARK = "__YT_GPU_PEAK_MB__="
GPU_PEAK_FILE = ".gpu-peak"
GPU_PIDS_FILE = ".gpu-pids"
GPU_PERIOD = "0.2"


def job_script(mirror: str, job: str, binary: str, rest: str, *, dirs: list[str] = (),
               sample_gpu: bool = False) -> str:
    """The whole remote script: link the job dir from the mirror, enter it,
    make the output directories, run the engine.

    With `sample_gpu`, on a host that has nvidia-smi, the engine runs beside a sampler
    (every GPU_PERIOD s) that sums the framebuffer of the engine's own processes -- the
    job shell's descendants -- from `nvidia-smi pmon -s m`, which lists graphics processes
    too. After the run, accounting mode's per-process maximum is read as well (it sees a
    render shorter than a period). With neither, the rise of memory.used over a baseline
    taken just before the engine started is the fallback. The result is one GPU_MARK line
    on stderr: "<MiB> <pmon|accounted|baseline-delta>". The sampler writes nothing to
    stdout, is killed on every exit path (a trap), and the engine's exit code is kept; a
    host without nvidia-smi runs the engine exactly as an unsampled job does."""
    made = "".join(f"mkdir -p {shlex.quote(d)} && " for d in dirs)
    head = f"{link_job_script(mirror, job)} && cd {shlex.quote(job)} && {made}"
    plain = f"{binary} {rest}"
    if not sample_gpu:
        return f"{head}{plain}"
    tree = ("ps -eo pid=,ppid= 2>/dev/null | awk -v r=$$ '{p[$1]=$2} END{for(i in p){x=i; "
            "while((x in p)&&x!=r){x=p[x]} if(x==r&&i!=r)print i}}' | tr '\\n' ' '")
    pmon = ("nvidia-smi pmon -c 1 -s m 2>/dev/null | awk -v t=\"$t\" 'BEGIN{n=split(t,a,\" \"); "
            "for(i=1;i<=n;i++)w[a[i]]=1} /^# *gpu/{for(i=1;i<=NF;i++)if($i==\"fb\")c=i-1; next} "
            "/^#/{next} c&&($2 in w){s+=$c+0} END{print s+0}'")
    accounted = ("nvidia-smi --query-accounted-apps=pid,max_memory_usage --format=csv,noheader,nounits "
                 f"2>/dev/null | awk -v t=\"$(sort -u {GPU_PIDS_FILE} 2>/dev/null | tr '\\n' ' ')\" "
                 "'BEGIN{n=split(t,a,\" \"); for(i=1;i<=n;i++)w[a[i]]=1} "
                 "{gsub(/,/,\" \")} ($1 in w){s+=$2+0} END{print s+0}'")
    used = ("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null "
            "| awk '{s+=$1} END{print s+0}'")
    sampler = (f"( pm=0; mx=0; while kill -0 $$ 2>/dev/null; do t=$({tree}); "
               f"echo $t | tr ' ' '\\n' >> {GPU_PIDS_FILE}; "
               f"x=$({pmon}); [ \"$x\" -gt \"$pm\" ] && pm=$x; "
               f"x=$({used}); [ \"$x\" -gt \"$mx\" ] && mx=$x; "
               f"echo \"$pm $mx\" > {GPU_PEAK_FILE}; sleep {GPU_PERIOD} 2>/dev/null || sleep 1; "
               "done ) >/dev/null 2>&1")
    # per-process figures win over the baseline delta whenever either saw something
    done = (f"__gpu_done() {{ kill $s 2>/dev/null; wait $s 2>/dev/null; pm=0; mx=0; "
            f"read pm mx < {GPU_PEAK_FILE} 2>/dev/null; ac=$({accounted}); v=0; k=; "
            "if [ \"$pm\" -gt 0 ] || [ \"$ac\" -gt 0 ]; then "
            "if [ \"$pm\" -ge \"$ac\" ]; then v=$pm; k=pmon; else v=$ac; k=accounted; fi; "
            "elif [ $((mx - base)) -gt 0 ]; then v=$((mx - base)); k=baseline-delta; fi; "
            f"[ \"$v\" -gt 0 ] && echo {GPU_MARK}$v $k >&2; "
            f"rm -f {GPU_PEAK_FILE} {GPU_PIDS_FILE}; }}")
    sampled = (f"{done}; base=$({used}); {sampler} & s=$!; trap __gpu_done EXIT; "
               f"trap 'exit 143' INT TERM HUP; {plain}; c=$?; exit $c")
    return f"{head}if command -v nvidia-smi >/dev/null 2>&1; then {sampled}; else {plain}; fi"


def take_gpu_peak(proc) -> int | None:
    """The peak GPU memory (MiB) a sampled job printed, taken out of proc.stderr (so the
    marker is never shown or stored as an error); None when the job was not sampled or saw
    none. How it was measured is left on proc.gpu_source."""
    found = None
    kept = []
    proc.gpu_source = ""
    for line in (proc.stderr or "").splitlines(keepends=True):
        if line.startswith(GPU_MARK):
            value, _, source = line[len(GPU_MARK):].strip().partition(" ")
            found = parse_mb(value) or None
            proc.gpu_source = source
        else:
            kept.append(line)
    if found is not None:
        proc.stderr = "".join(kept)
    return found


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


def named_outputs(cmd: list[str]) -> tuple[str, ...]:
    """The project-relative paths a command is told to write (`-o`, `--report`), or none
    when it names no `-o` (an encode of a declared `--output` writes files the document
    names, so the pull-back falls back to PULL_BACK). Absolute and `..` paths are not
    the project's and are dropped."""
    if "-o" not in cmd[:-1]:
        return ()
    names: list[str] = []
    for n, token in enumerate(cmd[:-1]):
        if token in ("-o", "--report"):
            path = Path(cmd[n + 1])
            if not path.is_absolute() and ".." not in path.parts and str(path) != "." \
                    and str(path) not in names:
                names.append(str(path))
    return tuple(names)


def pull_back_args(host: str, remote: str, dest: Path,
                   patterns: tuple[str, ...] = PULL_BACK,
                   named: tuple[str, ...] = ()) -> list[str]:
    """rsync argv bringing a job's results from host:remote to dest.

    With `named` (see named_outputs) only those paths come back (a directory whole);
    otherwise the PULL_BACK patterns. Either way `--update`: the job directory is a
    copy of the mirror, so a report or asset the job did not write is older than the
    project's own if anything wrote it since, and an older copy never replaces a newer file.
    """
    head = ["rsync", "-az", "--update", "-e", SSH_COMMAND]
    tail = ["--exclude=*", f"{host}:{remote}/", f"{dest}/"]
    if named:
        rules: list[str] = []
        for name in named:
            parts = Path(name).parts
            rules += [f"--include=/{'/'.join(parts[:k])}/" for k in range(1, len(parts))]
            rules += [f"--include=/{name}", f"--include=/{name}/***"]
        return head + list(dict.fromkeys(rules)) + tail
    return head + ["--include=*/"] + [f"--include={p}" for p in patterns] + tail


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
    gpu_mem_mb: int = 0     # the GPU's total memory (nvidia-smi), 0 when it has none or did not say
    ffprobe: str = ""       # ffprobe there, which the delivery gate needs
    disk_free_gb: int = 0   # free GB on the filesystem that holds REMOTE_ROOT; 0 when it did not say
    sha256: str = ""        # the binary's hash there, "" when it could not be read
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


def parse_mb(text: str) -> int:
    """A whole number of MiB out of nvidia-smi's `csv,noheader,nounits` text; 0 when it is not one."""
    match = re.match(r"\s*(\d+)\s*$", text or "")
    return int(match.group(1)) if match else 0


def parse_gb(text: str) -> int:
    """A whole number of GB out of `df -BG` text such as `14G`; 0 when it is not one."""
    match = re.match(r"\s*(\d+)G?\s*$", text or "")
    return int(match.group(1)) if match else 0


def probe(addr: str, *, timeout: float = 12.0) -> Host:
    """One ssh round trip: engine, RAM, cores, GPU. Cached for the process;
    `ssh_alive` is what notices a host that went away afterwards."""
    if addr in _HOSTS:
        return _HOSTS[addr]
    binary_expr = os.environ.get("RS_RENDER_REMOTE_BIN") or REMOTE_BIN_LOOKUP
    script = (f"B={binary_expr}; if [ -x \"$B\" ]; then echo \"ENGINE=$B\"; "
              f"echo \"VERSION=$($B --version 2>/dev/null | head -1)\"; "
              f"echo \"SHA=$(sha256sum \"$B\" 2>/dev/null | cut -d' ' -f1)\"; else echo ENGINE=; fi; "
              "echo \"RAM=$(free -g | awk 'NR==2{print $2}')\"; echo \"CORES=$(nproc)\"; "
              "echo \"GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)\"; "
              "echo \"GPUMEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null"
              " | sort -n | head -1)\"; "
              "echo \"FFPROBE=$(command -v ffprobe 2>/dev/null)\"; "
              # the mirrors and every job live under REMOTE_ROOT, so its parent's
              # filesystem is the one an encode fills (docs/compute.md: disk on .7)
              f"echo \"DISK=$(df -P -BG {shlex.quote(str(Path(REMOTE_ROOT).parent))} 2>/dev/null"
              " | awk 'NR==2{print $4}')\"")
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
        elif key == "SHA":
            host.sha256 = value
        elif key == "RAM":
            host.ram_gb = int(value or 0)
        elif key == "CORES":
            host.cores = int(value or 0)
        elif key == "GPU":
            host.gpu = value
        elif key == "GPUMEM":
            host.gpu_mem_mb = parse_mb(value)
        elif key == "FFPROBE":
            host.ffprobe = value
        elif key == "DISK":
            host.disk_free_gb = parse_gb(value)
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
        script = job_script(mirror, remote, binary, rest, dirs=output_dirs(relative),
                            sample_gpu=bool(known and known.gpu))
        if verbose:
            print(f"  ssh {host} {script}", file=sys.stderr)
        proc, settled = _run_detached(host, script, remote, verbose=verbose)
        proc.gpu_peak_mb = take_gpu_peak(proc)
        if not settled:
            return proc                 # outcome unknown: no pull-back, no removal
        refuse_remote()
        pulled = subprocess.run(pull_back_args(host, remote, project_root, named=named_outputs(relative)),
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


def validate_findings(report: dict) -> list[str]:
    """The diagnostics of one `validate --format json` run, as finding strings.

    Reads both shapes the engine has printed: the bare `{"valid", "files"}`
    of engines before 0.6.0, and the `scene-render-cli/1` envelope since,
    whose `result` holds the same files. The envelope's own `diagnostics`
    (findings about no one file) and an `error` that is not plain invalidity
    (a usage or I/O failure) are reported too, so nothing the engine said is
    dropped on the floor.
    """
    result = report.get("result")
    body = result if isinstance(result, dict) else report
    findings: list[str] = []

    def line(diag) -> str:
        if not isinstance(diag, dict):
            return f"error[]: {diag}"
        sev = str(diag.get("severity") or "error").lower()
        code = diag.get("code") or ""
        at = diag.get("label") or diag.get("id") or diag.get("node") or diag.get("path") or ""
        msg = diag.get("message") or str(diag)
        return f"{sev}[{code}] ({at}): {msg}" if at else f"{sev}[{code}]: {msg}"

    for file in body.get("files") or []:
        for diag in (file.get("diagnostics") if isinstance(file, dict) else None) or []:
            findings.append(line(diag))
    for diag in report.get("diagnostics") or []:
        text = line(diag)
        if text not in findings:
            findings.append(text)
    error = report.get("error")
    if isinstance(error, dict) and error.get("kind") not in (None, "invalid"):
        findings.append(f"error[{error.get('kind')}]: {error.get('message') or error}")
    elif isinstance(error, str) and error:
        findings.append(f"error[]: {error}")
    return findings


def _validate_output(stdout: str, stderr: str, returncode: int) -> list[str]:
    """What one validate run (here or on a host) amounts to, as findings."""
    try:
        report = json.loads(stdout[stdout.index("{"):])
    except ValueError:  # no `{`, or not JSON: every engine prints a report, even on success
        text = (stderr or stdout).strip() or "no output"
        return [f"error: sdk:engine-failed: 'validate' printed no report "
                f"(exit {returncode}): {text[-400:]}"]
    if not isinstance(report, dict):
        return [f"error: sdk:engine-failed: 'validate' printed {type(report).__name__}, not a report"]
    findings = validate_findings(report)
    result = report.get("result")
    body = result if isinstance(result, dict) else report
    if not findings and (report.get("ok") is False or body.get("valid") is False):
        # the engine said invalid and named nothing: still not a pass
        findings.append("error: sdk:engine-failed: 'validate' reports the document "
                        "invalid but lists no diagnostics")
    if returncode not in (0, 1) and not findings:
        text = (stderr or stdout).strip()
        findings.append(f"error: sdk:engine-failed: 'validate' exited {returncode}: {text[-400:]}")
    return findings


def engine_validate_local(xml_bytes: bytes, binary: Path, *, timeout: float = 120.0
                          ) -> list[str]:
    """`validate --format json --no-assets` with the binary on this machine.

    The bytes go to a temporary file (so `--no-assets`: relative paths could
    not resolve there) that is removed afterwards. An engine that cannot run,
    times out, or prints no report is one `error: sdk:engine-failed` finding.
    """
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(prefix=".yt-validate-", suffix=".scene.xml",
                                         delete=False) as handle:
            handle.write(xml_bytes)
            name = handle.name
    except OSError as exc:
        return [f"error: sdk:engine-failed: cannot write the temporary document: {exc}"]
    try:
        proc = subprocess.run([str(binary), "validate", "--format", "json", "--no-assets", name],
                              capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"error: sdk:engine-failed: {exc}"]
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    return _validate_output(proc.stdout or "", proc.stderr or "", proc.returncode)


def engine_validate(xml_bytes: bytes, *, host: str = "", timeout: float = 120.0
                    ) -> list[str] | None:
    """The engine's own validation of a document, as finding strings.

    Level 3 of the SDK's ladder: the schema and the SDK's checks let a
    document through that the engine refuses (a path with no box, for one),
    so a document is not trusted until the engine has read it. Runs
    `validate` against a local binary when there is one (engine_validate_local),
    else ships the bytes to RS_RENDER_HOST and runs it there. Both read the
    report through validate_findings, so a finding reads the same wherever the
    engine ran. None means no engine could be asked; the caller decides what
    that is worth.
    """
    binary = engine()
    if binary is not None:
        return engine_validate_local(xml_bytes, binary, timeout=timeout)
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
    return _validate_output(proc.stdout.decode("utf-8", "replace"),
                            proc.stderr.decode("utf-8", "replace"), proc.returncode)


#: Per-job report statuses (AUTO-32).
STATUSES = ("rendered", "failed", "timeout", "blocked-inputs")

_SHA_MEMO: dict[tuple, str] = {}
_VERSION_MEMO: dict[tuple, str] = {}


def _file_sha(path: Path) -> str:
    """sha256 of a file, "" when it is not readable; remembered per (path, mtime, size)."""
    try:
        st = path.stat()
        memo = (str(path), st.st_mtime_ns, st.st_size)
        if memo not in _SHA_MEMO:
            h = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            _SHA_MEMO[memo] = h.hexdigest()
        return _SHA_MEMO[memo]
    except OSError:
        return ""


def _binary_version(path: Path) -> str:
    try:
        st = path.stat()
        memo = (str(path), st.st_mtime_ns, st.st_size)
        if memo not in _VERSION_MEMO:
            proc = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10)
            _VERSION_MEMO[memo] = (proc.stdout or "").strip().splitlines()[0] if (
                proc.returncode == 0 and (proc.stdout or "").strip()) else ""
        return _VERSION_MEMO[memo]
    except (OSError, subprocess.SubprocessError):
        return ""


def engine_info(host: str = "", binary: Path | None = None) -> dict:
    """The engine that ran (or would run) a job: {version, sha256, path}. A host's
    comes from its probe; a local one is read from the file. Empty strings when unknown."""
    if host:
        known = _HOSTS.get(host)
        engine_path = getattr(known, "engine", "")
        return {"version": getattr(known, "version", ""), "sha256": getattr(known, "sha256", ""),
                "path": f"{host}:{engine_path}" if engine_path else host}
    binary = binary if binary is not None else engine()
    if binary is None or not Path(binary).is_file():
        return {"version": "", "sha256": "", "path": str(binary or "")}
    return {"version": _binary_version(Path(binary)), "sha256": _file_sha(Path(binary)),
            "path": str(binary)}


def _local_refs(text: str) -> list[str]:
    """Relative file paths a document names (src=, environment=, the material maps...)."""
    from .vfx import FILE_ATTRS
    return [m.group(1) for m in re.finditer(r'\b(?:' + "|".join(FILE_ATTRS) + r')="([^"]+)"', text)
            if "://" not in m.group(1) and not m.group(1).startswith(("#", "/"))]


def missing_inputs(doc_text: str, base: Path, root: Path | None = None) -> list[str]:
    """Files the document names that are not on disk, relative to `root` (default `base`)."""
    root = root or base
    out: list[str] = []
    for ref in _local_refs(doc_text):
        path = Path(os.path.normpath(base / ref))
        if not path.exists():
            try:
                shown = path.relative_to(root).as_posix()
            except ValueError:
                shown = path.as_posix()
            if shown not in out:
                out.append(shown)
    return out


def _bundle_missing(doc_text: str, bundle: dict[str, bytes], doc_path: str) -> list[str]:
    base = Path(doc_path).parent
    out: list[str] = []
    for ref in _local_refs(doc_text):
        if ref.endswith(".scene.xml"):
            continue            # another document: shipped, or the engine's own business
        rel = os.path.normpath((base / ref).as_posix())
        if rel not in bundle and rel not in out:
            out.append(rel)
    return out


def job_report(job: str, status: str | None = None, *, exit_code: int = 0, stdout: str = "",
               stderr: str = "", seconds: float = 0.0, envelope: dict | None = None,
               missing: list[str] | tuple = (), timeout_seconds: float | None = None,
               engine: dict | None = None, message: str = "", command: str | None = None,
               gpu_memory: dict | None = None) -> dict:
    """The one report shape of every render job: the engine's scene-render-cli/1
    envelope (kept when it printed one, made when it did not) plus

        status          rendered | failed | timeout | blocked-inputs
        job             encode | still | audio | validate | frames | ...
        missing_inputs  the files a blocked job lacked
        timeout         true (with timeout_seconds) when the job ran out of time
        seconds         spent
        engine          {version, sha256, path}
        outcome_unknown true when a detached job never reported
        message         a line on what went wrong, empty when nothing did
        gpu_memory      {peak_mb, programme_seconds, host, host_total_mb} when the job ran
                        sampled on a GPU host (see gpu_measurement); absent otherwise
    """
    env = dict(envelope if envelope is not None else _envelope(stdout))
    unknown = "remote job outcome unknown" in (stderr or "")
    lost_deadline = "remote job lost: it did not finish within" in (stderr or "")
    timed_out = status == "timeout" or lost_deadline
    if status is None:
        if timed_out:
            status = "timeout"
        elif missing:
            status = "blocked-inputs"
        elif exit_code == 0 and not unknown and env.get("ok", True) is not False:
            status = "rendered"
        else:
            status = "failed"
    ok = status == "rendered"
    if not message and not ok:
        tail = (stderr or "").strip() or (stdout if not env else "").strip()
        message = tail[-300:] if tail else f"{job} {status} (exit {exit_code})"
    if status == "blocked-inputs" and not message:
        message = "missing inputs: " + ", ".join(missing)
    env.setdefault("format", "scene-render-cli/1")
    env["ok"] = ok
    env["exit_code"] = 0 if ok else (exit_code or 1)
    env.setdefault("command", command or job)
    env.setdefault("diagnostics", [])
    env.setdefault("result", {"outputs": []})
    env.update({"status": status, "job": job, "missing_inputs": list(missing),
                "timeout": bool(timed_out), "seconds": round(float(seconds), 3),
                "engine": engine or {"version": "", "sha256": "", "path": ""},
                "outcome_unknown": unknown, "message": message})
    if timed_out and timeout_seconds is not None:
        env["timeout_seconds"] = timeout_seconds
    if gpu_memory:
        env["gpu_memory"] = gpu_memory
    return env


def write_job_report(path: Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_json(path, report)


def still_cache_dir() -> Path:
    """Where stills are cached: $YT_STILL_CACHE, else .cache/stills of the working
    project: the nearest directory from here up that holds an idea.json (`.cache/` never
    travels to a host, see SYNC_EXCLUDES). Outside any project it is a per-user scratch
    directory, so nothing is written into an arbitrary checkout."""
    chosen = os.environ.get("YT_STILL_CACHE")
    if chosen:
        return Path(chosen)
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / workspace.ARTIFACTS["idea"]).is_file():
            return folder / ".cache" / "stills"
    import tempfile
    return Path(tempfile.gettempdir()) / f"yt-still-cache-{os.getuid()}" / "stills"


def still_fingerprint(xml_bytes: bytes, files: dict[str, bytes], doc_path: str, at: float,
                      quality: str, engine_id: str, options: str = "json",
                      extra: dict[str, bytes] | None = None) -> str:
    """Everything a still depends on: the scene, every shipped file's bytes, every other
    file the scene references (`extra`, see reference_files), the engine, the time, the
    quality and the options."""
    h = hashlib.sha256()

    def part(label: str, data: bytes) -> None:
        h.update(label.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")
    part("scene", xml_bytes)
    part("doc", doc_path.encode())
    for rel in sorted(files):
        part("file:" + rel, files[rel])
    for name in sorted(extra or {}):
        part("ref:" + name, (extra or {})[name])
    part("engine", engine_id.encode())
    part("time", repr(float(at)).encode())
    part("quality", quality.encode())
    part("options", options.encode())
    return h.hexdigest()


#: Attributes besides vfx.FILE_ATTRS that name a file the engine opens.
REFERENCE_ATTRS = ("fontFile",)
MODEL_SUFFIXES = (".gltf", ".obj", ".usd", ".usda", ".mtl")


def reference_files(text: str, base: Path, _seen: set[Path] | None = None) -> dict[str, bytes] | None:
    """Every local file a document names, whatever the caller ships with it: {resolved
    path: bytes}. Textures, models (with the directory their buffers and materials live
    in), HDRIs, fonts and included documents (and what those name). None when a
    reference cannot be read -- missing or unreadable -- because then the still's
    inputs are not known and it must not be cached."""
    from .vfx import FILE_ATTRS
    seen = _seen if _seen is not None else set()
    out: dict[str, bytes] = {}
    pattern = r'\b(?:' + "|".join(FILE_ATTRS + REFERENCE_ATTRS) + r')="([^"]+)"'
    for m in re.finditer(pattern, text):
        ref = m.group(1)
        if "://" in ref or ref.startswith("#"):
            continue
        path = Path(os.path.normpath(base / ref))
        if path in seen:
            continue
        seen.add(path)
        try:
            out[str(path)] = path.read_bytes()
            if path.suffix.lower() in MODEL_SUFFIXES:
                for sibling in sorted(path.parent.rglob("*")):
                    if sibling.is_file() and str(sibling) not in out:
                        out[str(sibling)] = sibling.read_bytes()
            elif ref.endswith(".scene.xml"):
                inner = reference_files(out[str(path)].decode("utf-8", "replace"), path.parent, seen)
                if inner is None:
                    return None
                out.update(inner)
        except OSError:
            return None
    return out


def _engine_id(host: str, cache: Path) -> tuple[str, dict]:
    """(identity, info) of the engine a still would run on, found without ssh: a local
    binary's hash, or a host's version and hash from its probe -- this process's, or the
    last one any process stored. ("" when unknown: then nothing is cached.)"""
    if host:
        info = engine_info(host)
        if not (info["version"] or info["sha256"]):
            try:
                stored = json.loads((cache / "engines.json").read_text(encoding="utf-8"))
                info = stored.get(host) or info
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        ident = f"{info['version']}|{info['sha256']}" if info["version"] or info["sha256"] else ""
        return ident, info
    info = engine_info("")
    return (f"local|{info['sha256']}" if info["sha256"] else ""), info


def engine_identity(host: str = "", cache_dir: Path | str | None = None) -> str:
    """The identity a cache keys a job on `host` by (see _engine_id), found
    without ssh; "" when it is not known, and then nothing may be cached."""
    cache = Path(cache_dir) if cache_dir else still_cache_dir()
    return _engine_id(host, cache)[0]


def _remember_engine(cache: Path, host: str, info: dict) -> None:
    if not (info.get("version") or info.get("sha256")):
        return
    try:
        path = cache / "engines.json"
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if stored.get(host) != info:
            stored[host] = info
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _cache_message(key: str, cache: Path, host: str) -> str:
    try:
        where = (cache / f"{key}.host").read_text(encoding="utf-8").strip()
    except OSError:
        where = ""
    return f"rendered from cache ({key[:12]}); rendered earlier on {where or host or 'this machine'}"


def cached_still(xml_bytes: bytes, at: float, *, host: str = "", quality: str = "draft",
                 files: dict[str, bytes] | None = None, doc_path: str = "doc.scene.xml",
                 cache_dir: Path | str | None = None,
                 extra: dict[str, bytes] | None = None) -> tuple[bytes, str] | None:
    """The cached frame for this still, or None: (png, message), found with no ssh and
    no lock. With no host given every configured host (else this machine) is tried, by
    the engine identity last known for it; the fingerprint is engine_render_still's."""
    cache = Path(cache_dir) if cache_dir else still_cache_dir()
    for h in ([host] if host else host_names() or [""]):
        ident, _ = _engine_id(h, cache)
        if not ident:
            continue
        key = still_fingerprint(xml_bytes, files or {}, doc_path, at, quality, ident, extra=extra)
        hit = cache / f"{key}.png"
        if hit.is_file():
            return hit.read_bytes(), _cache_message(key, cache, h)
    return None


def engine_render_still(xml_bytes: bytes, at: float, *, host: str = "",
                        quality: str = "draft", files: dict[str, bytes] | None = None,
                        doc_path: str = "doc.scene.xml", timeout: float = 600.0,
                        cache_dir: Path | str | None = None,
                        extra: dict[str, bytes] | None = None,
                        cacheable: bool = True) -> tuple[bytes | None, str]:
    """One frame of a document, rendered by the engine: (png, message).

    Validation cannot see a resource limit, a camera that frames nothing or a
    solver that refuses its grid; a frame can. This is the gate an authored
    document has to pass before it counts as authored. `files` are shipped
    with the document, keyed by path relative to the working directory the
    engine runs in (the document itself is `doc_path`); a symbol document
    normally needs none. The message carries the engine's diagnostics, one
    per line after the first, so a caller can show them to an author.

    A frame is cached by a fingerprint of the scene, every shipped file, the
    engine, the time and the quality under `cache_dir` (see still_cache_dir),
    and a hit is served here, before any ssh. Every outcome -- rendered,
    failed, timed out, blocked on a missing file -- leaves a job report in
    the cache's sibling reports/ directory, which `yt doctor` lists. `extra` are the
    other files the scene references (reference_files), folded into the fingerprint;
    with `cacheable=False` (a reference could not be read) the cache is neither
    read nor written.
    """
    cache = Path(cache_dir) if cache_dir else still_cache_dir()
    bundle: dict[str, bytes] = dict(files or {})
    bundle[doc_path] = xml_bytes
    started = time.monotonic()
    state: dict = {"key": "", "info": engine_info(host) if host else {"version": "", "sha256": "", "path": ""}}

    def key_for(h: str) -> tuple[str, str]:
        ident, state["info"] = _engine_id(h, cache)
        state["key"] = still_fingerprint(xml_bytes, files or {}, doc_path, at, quality, ident,
                                         extra=extra)
        return state["key"], ident

    def report(status: str, message: str = "", **kw) -> None:
        rep = job_report("still", status, seconds=time.monotonic() - started, message=message,
                         engine=state["info"], command="render", **kw)
        rep["fingerprint"] = state["key"]
        try:
            write_job_report(cache.parent / "reports" / f"still-{state['key'][:12]}.json", rep)
        except OSError:
            pass

    def lookup(h: str) -> tuple[bytes, str] | None:
        key, ident = key_for(h)
        hit = cache / f"{key}.png"
        if cacheable and ident and hit.is_file():
            report("rendered", f"served from the still cache ({key[:12]})")
            return hit.read_bytes(), _cache_message(key, cache, h)
        return None

    def store(png: bytes) -> None:
        if not cacheable or not (state["info"]["version"] or state["info"]["sha256"]):
            return
        try:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{state['key']}.host").write_text(host or "this machine", encoding="utf-8")
            tmp = cache / f".{state['key']}.{os.getpid()}.tmp"
            tmp.write_bytes(png)
            os.replace(tmp, cache / f"{state['key']}.png")
        except OSError:
            pass

    missing = _bundle_missing(xml_bytes.decode("utf-8", "replace"), bundle, doc_path)
    if missing:
        key_for(host)
        report("blocked-inputs", missing=missing)
        return None, "error: blocked on missing inputs: " + ", ".join(missing)
    cached = lookup(host)
    if cached:
        return cached
    host = host or remote_host(None, "still")
    if host:
        probe(host)
        state["info"] = engine_info(host)
        _remember_engine(cache, host, state["info"])
        cached = lookup(host)
        if cached:
            return cached
    binary = engine()
    tag = f"still-{os.getpid()}-{uuid.uuid4().hex[:10]}"     # parallel stills must not share it
    if binary is None and not host:
        key_for(host)
        report("failed", "no engine reachable")
        return None, "no engine reachable"
    if binary is not None and not host:
        work = Path(REMOTE_ROOT) / tag
        try:
            for rel, data in bundle.items():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_bytes(data)
            try:
                proc = subprocess.run([str(binary), "render", doc_path, "-t", str(at), "-o",
                                       "frame.png", "--json"], cwd=work, capture_output=True,
                                      text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                report("timeout", f"the still ran past {timeout:g} s", timeout_seconds=timeout,
                       exit_code=124)
                raise
            png = (work / "frame.png").read_bytes() if (work / "frame.png").is_file() else None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        message = _still_message(proc.returncode, proc.stdout, proc.stderr, "rendered locally")
        if png is not None and proc.returncode == 0:
            store(png)
            report("rendered", envelope=_envelope(proc.stdout))
        else:
            report("failed", message[:300], exit_code=proc.returncode,
                   envelope=_envelope(proc.stdout))
        return png, message
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
        message = f"render host unreachable: {exc}"
        if isinstance(exc, subprocess.TimeoutExpired):
            report("timeout", message, timeout_seconds=timeout, exit_code=124)
        else:
            report("failed", message, exit_code=255)
        return None, message
    err = proc.stderr.decode("utf-8", "replace")
    engine_json, _, messages = err.partition("\n")
    png = proc.stdout if proc.stdout[:8] == b"\x89PNG\r\n\x1a\n" else None
    message = _still_message(proc.returncode, engine_json, messages, f"rendered on {host}")
    if png is not None and proc.returncode == 0:
        store(png)
        report("rendered", envelope=_envelope(engine_json))
    elif proc.returncode == 124 or (proc.returncode == 137 and
                                    time.monotonic() - started >= timeout - 10):
        report("timeout", f"the still ran past {timeout:g} s on {host}", timeout_seconds=timeout,
               exit_code=proc.returncode)
    else:
        report("failed", message[:300], exit_code=proc.returncode, envelope=_envelope(engine_json))
    return png, message


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


def _envelope(text: str) -> dict:
    """The engine's JSON report from a job's stdout, {} when it printed none."""
    text = (text or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return report if isinstance(report, dict) else {}


def _outputs(report: dict) -> list[dict]:
    outs = (report.get("result") or {}).get("outputs") or []
    return [o for o in outs if isinstance(o, dict)]


def merge_slice_reports(slices: list[dict], mix: dict, video: str, start: float,
                        end: float) -> dict:
    """The scene-render-cli/1 envelope of a sliced encode, as one encode's.

    One output stands for the joined file: its path, range and duration are
    the join's, frames add up, the quality is the lowest any slice ran at,
    findings and warnings are every slice's said once, and the loudness
    fields are the mix's (the slices carry no audio of their own).
    """
    keys = ("findings", "evaluation_warnings", "unsupported", "warnings")
    parts = [o for rep in slices for o in _outputs(rep)]
    output: dict = {"id": "main", "path": video, "files": [video],
                    "range": [start, end], "duration": round(end - start, 3)}
    tiers = [str(o["quality"]) for o in parts if o.get("quality")]
    order = ["draft", "preview", "final"]
    if tiers:
        output["quality"] = min(tiers, key=lambda t: order.index(t) if t in order else -1)
    frames = [o["frames"] for o in parts if isinstance(o.get("frames"), int)]
    if frames:
        output["frames"] = sum(frames)
    fps = next((o["fps"] for o in parts if o.get("fps")), None)
    if fps:
        output["fps"] = fps
    for key in keys:
        said: set[str] = set()
        output[key] = []
        for o in parts:
            for item in o.get(key) or []:
                mark = json.dumps(item, sort_keys=True, default=str)
                if mark not in said:
                    said.add(mark)
                    output[key].append(item)
    audio = next(iter(_outputs(mix)), {})
    for key in ("loudness", "true_peak", "loudness_range", "short_term_max"):
        if key in audio:
            output[key] = audio[key]
    diagnostics: list = []
    for rep in [*slices, mix]:
        for d in rep.get("diagnostics") or []:
            if d not in diagnostics:
                diagnostics.append(d)
    ok = all(rep.get("ok", True) for rep in [*slices, mix])
    return {"format": "scene-render-cli/1", "ok": ok, "exit_code": 0 if ok else 1,
            "command": "encode", "diagnostics": diagnostics,
            "result": {"outputs": [output]}}


def gpu_measurement(peak_mb: int | None, programme_seconds: float | None = None, host: str = "",
                    host_total_mb: int = 0, source: str = "") -> dict | None:
    """What a sampled job recorded, for the report. An encode: {kind "encode", peak_mb,
    programme_seconds (the range it rendered), host, host_total_mb, source}. A still or
    frames job (no `programme_seconds`): the same with kind "still" and no seconds -- one
    frame's peak is not a rate. None when nothing was sampled or no GPU memory was seen."""
    if not peak_mb or (programme_seconds is not None and programme_seconds <= 0):
        return None
    block = {"kind": "still" if programme_seconds is None else "encode", "peak_mb": int(peak_mb)}
    if programme_seconds is not None:
        block["programme_seconds"] = round(float(programme_seconds), 3)
    block.update(host=host, host_total_mb=int(host_total_mb or 0), source=source)
    return block


def measured_mb_per_second(*reports: dict | None) -> float | None:
    """Peak GPU memory per second of programme, the largest over the encode reports that
    carry a `gpu_memory` block with the range they rendered; None when none does, so nothing
    is guessed. A still's block has no seconds and is never read as a rate."""
    best = None
    for report in reports:
        block = (report or {}).get("gpu_memory")
        if not isinstance(block, dict) or block.get("kind") == "still":
            continue
        try:
            peak, seconds = float(block["peak_mb"]), float(block["programme_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if peak > 0 and seconds > 0:
            best = max(best or 0.0, peak / seconds)
    return best


def still_peak_mb(*reports: dict | None) -> int | None:
    """The largest still-only peak (MiB) among the reports; informative, never a rate."""
    peaks = [int(b["peak_mb"]) for b in ((r or {}).get("gpu_memory") for r in reports)
             if isinstance(b, dict) and b.get("kind") == "still" and b.get("peak_mb")]
    return max(peaks, default=None)


def memory_model(*reports: dict | None) -> tuple[float, float, str] | None:
    """(base, growth, basis): peak(seconds) = base + growth * seconds, in MiB and MiB/s.
    Most of an encode's peak is the fixed cost of one frame, so dividing it by the seconds
    rendered over-predicts a long film. With a still's peak (the largest) that is the base and
    growth = (encode peak - base) / encode seconds, the largest over the encodes, floored at 0.
    With no still but two encodes of different lengths, both terms are fitted from the shortest
    and the longest. With one encode and no still the model is the whole-peak rate with a
    base of 0 -- an upper bound, and the basis says so. None when there is no encode."""
    points = []
    for report in reports:
        block = (report or {}).get("gpu_memory")
        if not isinstance(block, dict) or block.get("kind") == "still":
            continue
        try:
            peak, seconds = float(block["peak_mb"]), float(block["programme_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if peak > 0 and seconds > 0:
            points.append((peak, seconds))
    if not points:
        return None
    still = still_peak_mb(*reports)
    if still:
        growth = max(max(0.0, (peak - still) / sec) for peak, sec in points)
        return float(still), growth, f"base {still} MiB from a still"
    (p1, s1), (p2, s2) = min(points, key=lambda x: x[1]), max(points, key=lambda x: x[1])
    if s2 > s1:
        growth = max(0.0, (p2 - p1) / (s2 - s1))
        base = max(0.0, p1 - growth * s1) if growth else max(p1, p2)
        return base, growth, f"fitted from encodes of {s1:g} s and {s2:g} s"
    return 0.0, measured_mb_per_second(*reports) or 0.0, \
        "no still to give the base: whole-peak rate, an upper bound"


def plan_slices(slices: int, start: float, end: float, hosts: list[Host], *,
                model: tuple[float, float, str] | None, still_peak: int | None = None,
                headroom: float = 0.8) -> tuple[int, str]:
    """(count, why): at least `slices`, and enough that one slice's GPU memory (`model`'s base
    plus its growth per second times the slice's length) fits the smallest usable host's GPU
    memory times `headroom`; both terms are printed. A still alone (`still_peak`, no encode)
    is one frame, not a model, so it is ignored and the line says so. With no model, or no host
    that reports its GPU memory, the requested count and a line saying so. A base that alone
    exceeds the GPU cannot be cured by slicing: the count is kept and the line says so."""
    if not model:
        ignored = (f" (a still's peak of {still_peak} MiB is one frame, not a rate: ignored)"
                   if still_peak else "")
        return slices, (f"GPU memory not measured as a rate (no encode report has gpu_memory)"
                        f"{ignored}: keeping {slices} slice(s)")
    totals = [h.gpu_mem_mb for h in hosts if h.usable and h.gpu_mem_mb > 0]
    if not totals or end <= start:
        return slices, (f"GPU memory measured but no usable host reports its GPU's total: "
                        f"keeping {slices} slice(s)")
    base, growth, basis = model
    smallest = min(totals)
    cap = smallest * headroom
    length = end - start
    why = (f"measured GPU memory = {base:.0f} MiB base + {growth:.1f} MiB/s of programme "
           f"({basis}); {length:.0f} s needs {base + growth * length:.0f} MiB against "
           f"{smallest} MiB x {headroom:g} on the smallest GPU")
    if base >= cap:
        return slices, why + ": the base alone does not fit, slicing cannot help"
    need = 1 if growth <= 0 else max(1, math.ceil(length * growth / (cap - base) - 1e-9))
    return max(slices, need), why


def memory_reports(report_path: Path) -> list[dict]:
    """The reports beside `report_path` that may carry a GPU measurement: the encode's own
    and the still / frames jobs' (render-still.json, render-frames.json)."""
    paths = [report_path] + [report_path.with_name(f"render-{job}.json") for job in ("still", "frames")]
    return [r for r in (read_json(p, warn=False) for p in paths if p.is_file()) if isinstance(r, dict)]


def encode_sliced(scene: str, project_root: Path, *, slices: int, quality: str | None,
                  start: float, end: float, strict: bool, verbose: bool,
                  report_path: Path | None = None, output: str | None = None,
                  keep_remote: bool = False, parallel: int | None = None) -> int:
    """A long encode across every usable host: video slices, one audio mix, join.

    Each slice is its own --start/--end encode (the only split that crosses a
    machine); the audio mix is rendered once on the host with the most RAM,
    because the engine assembles it whole whatever range is asked for; the
    slices are joined by stream copy and the mix muxed in, on that same host.
    The slices' and the mix's reports are merged into the render.json an
    unsliced encode writes (`report_path`, default the project's render.json).

    The slices live in `.slices/<key>/` (the mirror leaves it out): a failed run keeps
    the finished ones, and a run with the same scene, shipped files, engines and range reuses
    them (any changed file, or a reference that cannot be read, reuses nothing); the join
    removes the directory once the film is written. `output` NAME writes NAME.mp4
    (with the report the caller names) instead of video.mp4; `keep_remote` leaves the
    join's job directory on the host.
    """
    report_path = report_path or project_root / "render.json"
    film = f"{Path(output).stem}.mp4" if output else "video.mp4"
    began = time.monotonic()

    def fail(message: str, host: str = "") -> int:
        """Every way out of the encode leaves the same report shape a success does -- beside
        render.json, not over it: the film already on disk keeps the report that describes it."""
        write_job_report(report_path.with_name(report_path.stem + "-failed.json"), job_report(
            "encode", "failed", exit_code=1, message=message, command="encode",
            seconds=time.monotonic() - began, engine=engine_info(host) if host else None))
        return 1

    usable = [h for h in hosts() if h.usable]
    if not usable:
        print("error: no host with the engine; set RS_RENDER_HOSTS", file=sys.stderr)
        return fail("no host with the engine; set RS_RENDER_HOSTS")
    big = pick_host(usable, "audio")
    known = memory_reports(report_path)
    fitted, why = plan_slices(slices, start, end, usable, model=memory_model(*known),
                              still_peak=still_peak_mb(*known))
    if fitted != slices:
        print(f"  {slices} slice(s) asked, {fitted} used: {why}", file=sys.stderr)
        slices = fitted
    elif "not measured" in why or "no usable host" in why:
        print(f"  {why}", file=sys.stderr)
    ranges = _slice_ranges(start, end, slices)
    peaks: dict[int, dict] = {}
    if verbose:
        print(f"  {slices} slice(s) over {', '.join(h.addr for h in usable)}; audio mix on {big.addr}",
              file=sys.stderr)
    rel_scene = str(Path(scene).resolve().relative_to(project_root.resolve()))
    try:
        scene_bytes = Path(scene).read_bytes()
    except OSError:
        scene_bytes = b""
    # Keyed like the still cache: the scene, every file it ships or includes, the engines
    # and the options. A reference that cannot be read leaves the inputs unknown: no reuse.
    refs = reference_files(scene_bytes.decode("utf-8", "replace"), Path(scene).resolve().parent)
    engines = "|".join(sorted(f"{h.addr}={engine_info(h.addr)['version']}:{engine_info(h.addr)['sha256']}"
                              for h in usable))
    key = still_fingerprint(scene_bytes, {}, rel_scene, start, quality or "", engines,
                            options=repr((strict, end, slices, parallel)),
                            extra=refs)[:12] if refs is not None else ""
    reusable = bool(key)
    key = key or hashlib.sha1(os.urandom(16)).hexdigest()[:12]
    rel_dir = f".slices/{key}"
    slice_dir = project_root / rel_dir

    def one(i: int, rng: tuple[float, float]) -> tuple[int, int, str, dict]:
        host = usable[i % len(usable)]
        out = f"{rel_dir}/slice-{i:02d}.mp4"
        done = project_root / out
        kept = done.with_suffix(".json")             # the slice's own report, written once it succeeded
        if reusable and done.is_file() and done.stat().st_size and kept.is_file():
            envelope = read_json(kept, warn=False)
            if isinstance(envelope, dict):
                if verbose:
                    print(f"  slice {i}: reusing {out}", file=sys.stderr)
                return (i, 0, "", envelope)
        cmd = ["scene-render", "encode", rel_scene, "-o", out, "--no-upload", "--json",
               "--start", str(rng[0]), "--end", str(rng[1])]
        if quality:
            cmd += ["--quality", quality]
        if strict:
            cmd.append("--strict")
        if parallel is not None:
            cmd += ["--parallel", str(parallel)]
        proc = _run_remote(cmd, project_root, host.addr, verbose=verbose)
        measured = gpu_measurement(getattr(proc, "gpu_peak_mb", None), rng[1] - rng[0],
                                   host.addr, host.gpu_mem_mb, getattr(proc, "gpu_source", ""))
        if measured:
            peaks[i] = measured
        envelope = _envelope(proc.stdout)
        if proc.returncode == 0 and done.is_file():
            write_json(kept, envelope)
        lines = [ln for ln in (proc.stderr or proc.stdout or "").strip().splitlines() if ln.strip()]
        return (i, proc.returncode, lines[-1].strip() if lines else "no output", envelope)

    with ThreadPoolExecutor(max_workers=len(usable)) as pool:
        results = list(pool.map(lambda ir: one(*ir), enumerate(ranges)))
    failed = [r for r in results if r[1] != 0]
    for i, code, tail, _ in failed:
        print(f"  slice {i} failed ({code}): {tail}", file=sys.stderr)
    if failed:
        return fail("; ".join(f"slice {i} failed ({code}): {tail}" for i, code, tail, _ in failed),
                    big.addr)

    # The mix runs in a directory that is kept until the join; the slices, which
    # the mirror leaves out, are sent there once, and the join happens there.
    remote = remote_dir(project_root.name, "audio", job_tag())
    mix = _run_remote(["scene-render", "encode", rel_scene, "-o", "mix.wav", "--no-upload", "--json",
                       "--start", str(start), "--end", str(end)], project_root, big.addr,
                      verbose=verbose, remote=remote, keep_remote=True)
    try:
        if mix.returncode == 0:
            refuse_remote()
            sent = subprocess.run(sync_up_args(slice_dir, big.addr, f"{remote}/slices", delete=False,
                                               excludes=("*.json",)),
                                  capture_output=True, text=True)
            if sent.returncode != 0:
                print(f"  could not send the slices to {big.addr}: {sent.stderr.strip()[-300:]}",
                      file=sys.stderr)
                return fail(f"could not send the slices to {big.addr}: {sent.stderr.strip()[-300:]}",
                            big.addr)
        if mix.returncode != 0:
            print(f"  audio mix failed: {(mix.stderr or mix.stdout).strip()[-200:]}", file=sys.stderr)
            return fail(f"audio mix failed: {(mix.stderr or mix.stdout).strip()[-200:]}", big.addr)
        join = (f"cd {shlex.quote(remote)} && "
                "printf 'file slices/slice-%02d.mp4\n' " + " ".join(str(i) for i in range(slices)) + " > slices.txt && "
                "ffmpeg -y -loglevel error -f concat -safe 0 -i slices.txt -c copy joined.mp4 && "
                "ffmpeg -y -loglevel error -i joined.mp4 -i mix.wav -map 0:v -map 1:a -c:v copy "
                f"-c:a aac -b:a 192k -shortest {shlex.quote(film)} && rm -f joined.mp4 && "
                f"ls -la {shlex.quote(film)}")
        refuse_remote()
        proc = subprocess.run(ssh_argv(big.addr, join), capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  join failed on {big.addr}: {proc.stderr.strip()[-300:]}", file=sys.stderr)
            return fail(f"join failed on {big.addr}: {proc.stderr.strip()[-300:]}", big.addr)
        pull = ["rsync", "-az", "-e", SSH_COMMAND, f"{big.addr}:{remote}/{film}", f"{project_root}/{film}"]
        refuse_remote()
        pulled = subprocess.run(pull, capture_output=True, text=True)
        if pulled.returncode != 0 or not (project_root / film).is_file():
            why = f"could not pull {film} from {big.addr}: {(pulled.stderr or '').strip()[-300:]}"
            print(f"  {why}", file=sys.stderr)
            return fail(why, big.addr)             # the slices stay: the next run joins again
    finally:
        if keep_remote:
            if verbose:
                print(f"  kept {big.addr}:{remote}", file=sys.stderr)
        else:
            remove_remote(big.addr, remote, verbose=verbose)
    shutil.rmtree(slice_dir, ignore_errors=True)
    try:
        slice_dir.parent.rmdir()
    except OSError:
        pass
    report = merge_slice_reports([r[3] for r in results], _envelope(mix.stdout),
                                 str(project_root.resolve() / film), start, end)
    report = job_report("encode", "rendered", envelope=report, command="encode",
                        seconds=time.monotonic() - began, engine=engine_info(big.addr),
                        gpu_memory=max(peaks.values(), default=None,
                                       key=lambda m: m["peak_mb"] / m["programme_seconds"]))
    write_job_report(report_path, report)
    print(f"wrote {report_path}", file=sys.stderr)
    print(f"wrote {project_root / film}  (joined from {slices} slices; "
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
    flags.ignore_model(p, "render")
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
    job = ("validate" if args.validate else "audio" if getattr(args, "audio_only", False)
           else "still" if (args.still is not None or args.frames) else "encode")
    report_path = report_path_for(Path(project.out("render")), job, args.output)
    if not scene:
        print("error: no scene in this project; run `yt build` first",
              file=sys.stderr)
        write_job_report(report_path, job_report(job, "blocked-inputs", exit_code=1,
                                                 missing=[Path(project.path("scene")).name]))
        return 1
    if args.prune_remote:
        names = host_names(args)
        if not names:
            print("error: no render hosts configured; nothing to prune", file=sys.stderr)
            return 1
        prune_remote(names, project.root.name, verbose=not args.quiet)
        return 0
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
    world = float(((read_json(project.source("build"), warn=False) or {}).get("scene") or {})
                  .get("duration") or 0)
    slices = args.slices
    end_ = args.end if args.end is not None else world
    if job == "encode" and host and not args.output and slices <= 1 and end_:
        # An unsliced encode of a long world is where the GPU ran out: ask the measurement.
        known = memory_reports(report_path)
        slices, why = plan_slices(1, args.start or 0.0, end_, [h for h in hosts() if h.usable],
                                  model=memory_model(*known),
                                  still_peak=still_peak_mb(*known))
        if slices > 1 and not args.quiet:
            print(f"  encoding in {slices} slices without --slices: {why}", file=sys.stderr)
        elif not args.quiet:
            print(f"  {why}", file=sys.stderr)
    if slices > 1 and job == "encode" and host:
        end = args.end if args.end is not None else world
        if not end:
            print("error: --slices needs the scene's duration; run `yt build` first or pass --end",
                  file=sys.stderr)
            return 1
        return encode_sliced(scene, project.root, slices=slices, quality=args.quality,
                             start=args.start or 0.0, end=end, strict=args.strict,
                             verbose=not args.quiet, report_path=report_path,
                             output=args.output, keep_remote=args.keep_remote,
                             parallel=args.parallel)

    began = time.monotonic()
    try:
        scene_text = Path(scene).read_text(encoding="utf-8", errors="replace")
    except OSError:
        scene_text = ""
    absent = missing_inputs(scene_text, Path(scene).resolve().parent, project.root.resolve())
    if absent:
        print(f"error: the scene names {len(absent)} file(s) that are not there: "
              f"{', '.join(absent[:6])}", file=sys.stderr)
        write_job_report(report_path, job_report(job, "blocked-inputs", exit_code=1,
                                                 missing=absent, command=job))
        return 1
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
    def leave(status: str, message: str, code: int = 1) -> None:
        """The outcome with no engine report to keep: still a report."""
        write_job_report(report_path, job_report(
            job, status, exit_code=code, message=message, command=job,
            seconds=time.monotonic() - began, engine=engine_info(host, binary if not host else None)))

    try:
        if host:
            proc = _run_remote(cmd, project.root, host, verbose=not args.quiet, job=job,
                               remote=remote, keep_remote=args.keep_remote)
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"error: could not reach {host}: {exc.stderr.strip()[:300]}",
              file=sys.stderr)
        leave("failed", f"could not reach {host}: {(exc.stderr or '').strip()[:300]}")
        return 1
    except BaseException as exc:        # abandoned by the caller, or a refused launch
        leave("failed", f"abandoned: {type(exc).__name__}: {str(exc)[:200]}")
        raise
    if proc.stderr.strip() and not args.quiet:
        print(proc.stderr.strip()[-4000:], file=sys.stderr)

    report = {}
    envelope = None
    if proc.stdout.strip().startswith("{"):
        try:
            report = json.loads(proc.stdout)
            if host:
                # The engine ran inside the remote copy, so its report names
                # files under the job's directory; the delivery gate matches
                # them against the project's own files, so localise them.
                report = _localise(report, remote, str(project.root.resolve()))
            envelope = report
        except json.JSONDecodeError:
            print(proc.stdout.strip()[-2000:])
    elif proc.stdout.strip():
        print(proc.stdout.strip()[-4000:])
    # Written whatever the outcome: the engine's envelope when it printed one,
    # a made one (status, seconds, engine, what went wrong) when it did not.
    write_job_report(report_path, job_report(
        job, exit_code=proc.returncode, stdout="" if envelope is not None else proc.stdout or "",
        stderr=proc.stderr or "", envelope=envelope if envelope is not None else {},
        seconds=time.monotonic() - began, command=job,
        engine=engine_info(host, binary if not host else None),
        gpu_memory=gpu_measurement(
            getattr(proc, "gpu_peak_mb", None),
            # a still or frames job is a peak only; an encode's rate is over the range it renders
            (args.end if args.end is not None else world) - (args.start or 0.0)
            if job == "encode" else None,
            host, next((h.gpu_mem_mb for h in hosts() if h.addr == host), 0) if host else 0,
            getattr(proc, "gpu_source", ""))))
    print(f"wrote {report_path}", file=sys.stderr)

    if report:
        print(f"\n{idea.title} — RENDER")
        for line in summarise(report):
            print(f"  {line}")

    if proc.returncode != 0:
        print(f"\nengine exited {proc.returncode}", file=sys.stderr)
    return proc.returncode
