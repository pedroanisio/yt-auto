"""Check the health of the toolchain and of every video, and say what to do next.

Nothing here calls a model. Beyond the render hosts' probe it reaches the
network only when asked: `--gateway-ping` makes one GET of Vercel AI
Gateway's credits. It reads what the stages wrote and
measures it: whether the environment can actually run each stage, how far each
video got, whether what was produced hangs together, and which command moves
it forward.

    yt doctor                       # the environment and every video
    yt doctor --title monty         # one video
    yt doctor --env                 # the toolchain only
    yt doctor --json health.json    # the same, as data
    yt doctor --env --gateway-ping  # and the gateway team's credit balance

Findings carry a level -- `fail` blocks publishing, `warn` needs a look,
`note` is information -- and, where there is one, the command that fixes them.
The exit code is 1 when anything fails, so it works as a gate in a script.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import env, gateway, house, keyframe, narrative, render, retention, spend, workspace
from .assets import licence_tier
from .core import read_json, timecode, write_json

#: Levels, in the order the report sorts them.
LEVELS = ("fail", "warn", "note", "ok")
MARK = {"fail": "x", "warn": "!", "note": "·", "ok": "+"}

#: Narration engines that prove the timeline but must not be published.
SCRATCH_ENGINES = ("stub", "espeak")

#: Below this many GB free where a host keeps its mirrors and jobs, an encode
#: is at risk: a 13-minute master is 1.7 GB, the mirror persists by design, and
#: the slices of one encode sit beside it (SKILL.md "Rendering").
LOW_DISK_GB = 10

#: (module, distribution, what it unlocks): the third-party packages the stages
#: import at runtime, found by a missing-import traceback otherwise.
RUNTIME_PACKAGES = (
    ("PIL", "Pillow", "the frame gate, yt qa's mechanical pass and the contact sheets"),
    ("numpy", "numpy", "yt sound --synth and the kokoro voice"),
    ("scipy", "scipy", "yt sound --synth"),
)

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

#: The manifests that record the house they were decided under. A decision's
#: cache digest holds the house block its stage was handed (`## all` and its
#: own section), so a manifest whose block differs from the one that loads now
#: is one whose stage asks its model again, and pays again, on the next run
#: (house.py).
HOUSE_MANIFESTS = ("styles.json", "script.json", "voice.json", "retime.json",
                   "props.json", "keyframe.json", "vfx.json", "sound.json",
                   "build.json", "gate.json", "review.json", "qa.json",
                   "improve.json", "pilot.json")

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

def check_environment(work: Path, *, ping: bool = False) -> Report:
    """The toolchain. Offline but for the render hosts' probe, unless `ping`
    (`--gateway-ping`) adds one GET of the gateway's credits."""
    report = Report("environment")

    claude = shutil.which("claude")
    if os.environ.get("YT_ENGINE") == "bridge":
        if os.environ.get("YT_BRIDGE_DIR") and importlib.util.find_spec("jsonschema"):
            report.add("note", "bridge", "chat bridge configured; an active chat worker "
                       "must service the queue (not an unattended model service)")
        else:
            report.add("fail", "bridge", "bridge needs YT_BRIDGE_DIR and jsonschema",
                       "set YT_BRIDGE_DIR; pip install -r requirements-bridge.txt")
    elif claude:
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
    named = os.environ.get("RS_RENDER_BIN")
    if named and (not binary or not binary.is_relative_to(named)):
        report.add("warn", "engine", f"RS_RENDER_BIN={named} holds no scene-render binary; "
                   + (f"using {binary} instead" if binary else "ignored"),
                   "point it at the binary, or at the unpacked release archive")
    if hosts:
        # The renderers are other machines; nothing is built here. One short
        # ssh per host asks for the engine, the memory and the GPU, a second
        # for its load and GPU memory in use right now, and the routing rule
        # (docs/compute.md: memory, not GPU, and not a saturated host) says
        # what each gets.
        check_hosts(report, hosts, {h.addr: render.host_health(h.addr) for h in hosts if h.usable})
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
        from . import gate as gatelib
        where = gatelib.audio_tool_paths(gatelib.tools_dir(None))
        absent = [name for name, path in where.items() if not path]
    except Exception:
        absent = []
    if absent:
        report.add("warn", "audio tools", f"missing: {', '.join(absent)}; the technical gate "
                   "stops at the first one", "restore ytstudio/audiotools from the repo, or put "
                   "the tools in the --tools-dir / YT_TOOLS_DIR directory")
    else:
        report.add("ok", "audio tools", "sr-audiocheck, sr-margin, sr-sibilance found "
                   "(the gate's technical pass can run them)")

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

    missing = [(module, dist, unlocks) for module, dist, unlocks in RUNTIME_PACKAGES
               if importlib.util.find_spec(module) is None]
    for module, dist, unlocks in missing:
        report.add("warn", "packages", f"{module} is not importable here; it serves {unlocks}",
                   f"pip install {dist}")
    if not missing:
        report.add("ok", "packages", ", ".join(m for m, _, _ in RUNTIME_PACKAGES)
                   + " importable (the frame checks, the synthesis and the offline voice can run)")

    ffmpeg = shutil.which("ffmpeg")
    report.add("ok" if ffmpeg else "note", "ffmpeg",
               f"ffmpeg at {ffmpeg}" if ffmpeg
               else "no ffmpeg: resampling and non-PCM stitching are unavailable")

    for name, unlocks in env.KNOWN.items():
        # Names only, never values. A service is unlocked by any variable
        # sharing its prefix: Freesound takes CLIENT_ID/SECRET as well as a key.
        # A two-letter first word names no service (AI_ would take in any
        # AI_* variable), so the gateway's prefix is AI_GATEWAY_.
        words = name.split("_")
        prefix = "_".join(words[:2] if len(words[0]) < 3 else words[:1]) + "_"
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
    check_gateway(report)
    check_gateway_engine(report)
    if ping:
        gateway_ping(report)
    review_log(report, work)
    retention_baseline(report, work)
    return report


#: Where a gateway key is made, for the fix of a missing or refused one: the
#: docs page, which leads to the dashboard's AI Gateway > API Keys tab (the
#: dashboard's own address carries the team's slug).
GATEWAY_KEYS_PAGE = "https://vercel.com/docs/ai-gateway/authentication-and-byok/api-keys"
#: The credits endpoint on the gateway's host: the team's balance and what it
#: has used, in USD. It reads the account and runs no model.
GATEWAY_CREDITS = "/v1/credits"
#: Seconds the ping waits; doctor is a quick look, not a retry loop.
GATEWAY_PING_TIMEOUT = 5


def _shown(url: str) -> str:
    """A URL as doctor may print it, and as the ping sends it: no user,
    password, query or fragment, since a proxy's URL may carry credentials
    (gateway.host never echoes it in an error for the same reason). The host
    is kept as written, so an IPv6 address keeps its brackets."""
    parts = urlsplit(url)
    netloc = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect, so a 3xx comes back as the HTTPError it is.
    urllib's own handler copies the request's headers onto the new request
    whatever its host or scheme, and the ping's carries the gateway key."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _key_fix() -> str:
    return (f"export {gateway.KEY} with a key from the AI Gateway API Keys page "
            f"({GATEWAY_KEYS_PAGE})")


def check_gateway(report: Report, environ: dict | None = None) -> None:
    """The claude and codex CLIs' route through Vercel AI Gateway
    (gateway.py), shown only when YT_GATEWAY is set to something other than
    off: with it unset no stage goes near the gateway and there is nothing to
    say. It asks the same functions the engines call, so a fail here is the
    GatewayError the first call of a run would raise.

    It also names what the route leaves behind in this shell, which Vercel's
    Claude Code page calls out (https://vercel.com/docs/ai-gateway/coding-agents/claude-code):
    a hand-set ANTHROPIC_BASE_URL, which yt replaces for the claude CLI and
    which, ending in /v1, is a 404 for anything else that reads it; and a
    direct ANTHROPIC_API_KEY, which key mode blanks for the CLI and
    subscription mode sends to the gateway in place of the login. Names and
    endpoints only: no key, and no proxy credential, is printed."""
    environ = os.environ if environ is None else environ
    if (environ.get(gateway.SWITCH) or "").strip().lower() in gateway.OFF:
        return
    area = "gateway"
    problems: list[str] = []
    for route in (gateway.claude_env, gateway.codex_config):
        try:
            route(environ)
        except gateway.GatewayError as exc:
            if str(exc) not in problems:    # a missing key fails both routes
                problems.append(str(exc))
    for problem in problems:
        fix = (_key_fix() if gateway.KEY in problem
               else f"fix what it names, or unset {gateway.SWITCH} to call the providers directly")
        report.add("fail", area, f"{gateway.SWITCH} is set but the route cannot run: {problem}",
                   fix)
    mode = claude = codex = None
    try:
        mode = gateway.auth_mode(environ)
        claude = gateway.claude_base_url(environ)
        codex = gateway.codex_base_url(environ)
    except gateway.GatewayError:
        pass                                # already a fail above
    if not problems:
        how = ("the claude CLI keeps its login and sends the key as a header"
               if mode == "subscription"
               else f"the claude CLI sends {gateway.KEY} as ANTHROPIC_AUTH_TOKEN")
        report.add("ok", area, f"claude CLI through {_shown(claude)}, codex CLI through "
                   f"{_shown(codex)} ({mode} mode: {how})")

    hand_set = (environ.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
    if hand_set.endswith("/v1"):
        report.add("warn", area, "ANTHROPIC_BASE_URL is set by hand and ends in /v1, which "
                   "the gateway answers with a 404 (the CLI's SDK adds /v1/messages itself); "
                   "yt replaces it for the claude CLI, but anything else that reads it fails",
                   f"unset ANTHROPIC_BASE_URL; name a proxy with {gateway.URL}")
    elif hand_set and claude and hand_set != claude:
        report.add("warn", area, "ANTHROPIC_BASE_URL is set by hand to another endpoint than "
                   "the gateway's; yt replaces it for the claude CLI, so it is not the one used",
                   f"unset ANTHROPIC_BASE_URL; name a proxy with {gateway.URL}")

    if (environ.get("ANTHROPIC_API_KEY") or "").strip():
        if mode == "subscription":
            report.add("warn", area, "ANTHROPIC_API_KEY is set, and in subscription mode the "
                       "claude CLI sends it to the gateway in place of your login",
                       "unset ANTHROPIC_API_KEY")
        else:
            report.add("note", area, "ANTHROPIC_API_KEY is set in this shell; yt blanks it "
                       "for the claude CLI, which would otherwise send it ahead of the "
                       "gateway key")


def gateway_ping(report: Report, environ: dict | None = None) -> None:
    """`yt doctor --gateway-ping`: one GET of the gateway's credits with the
    key as a Bearer token, for the team's `balance` and `total_used` (USD
    strings, team-wide, not this run's). It runs no model and spends no
    tokens; whether the endpoint itself is billed or rate limited the docs do
    not say. It runs whether or not YT_GATEWAY is set, since the gateway
    engine (AUTO-124) uses the same key without it.

    A refused key (401, 403) or another answer the operator can fix is a
    fail; a busy or failing gateway (429, 5xx) or no answer at all is a
    warning, since running again may pass. Only the status and the answer's
    `error.type` are printed, never its message, which might quote what was
    sent. Under YT_NO_REMOTE (the suite) nothing is sent.

    The key goes to the host YT_GATEWAY_URL names and nowhere else: a
    redirect is not followed (it is a fail naming the status), and the
    header is one urllib never copies onto another request. A user and
    password in YT_GATEWAY_URL are left out of the request, with a note:
    urllib would read them as part of the host name, and a proxy's
    credentials are the claude CLI's business, not the ping's."""
    environ = os.environ if environ is None else environ
    area = "gateway ping"
    if environ.get(render.NO_REMOTE_ENV):
        report.add("warn", area, f"not pinged: {render.NO_REMOTE_ENV} is set")
        return
    try:
        key = gateway.api_key(environ)
        host = gateway.host(environ)
    except gateway.GatewayError as exc:
        report.add("fail", area, f"cannot ping: {exc}",
                   _key_fix() if gateway.KEY in str(exc) else f"fix {gateway.URL}")
        return
    split = urlsplit(host)
    credentials = split.username is not None or split.password is not None
    shown = url = _shown(host) + GATEWAY_CREDITS
    if credentials:
        report.add("note", area, f"the user and password in {gateway.URL} are not sent by "
                   f"the ping, which asks {shown} with the gateway key alone")
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=GATEWAY_PING_TIMEOUT) as answer:
            raw = answer.read()
    except urllib.error.HTTPError as exc:
        kind = _error_type(exc)
        said = f"GET {shown} answered HTTP {exc.code}" + (f" ({kind})" if kind else "")
        if exc.code in (401, 403):
            why = ("the gateway refused the key, or the proxy wanted the user and "
                   "password the ping leaves out" if credentials
                   else "the gateway refused the key")
            report.add("fail", area, f"{said}: {why}",
                       f"check {gateway.KEY}: copy or create a key on the AI Gateway "
                       f"API Keys page ({GATEWAY_KEYS_PAGE})")
        elif 300 <= exc.code < 400:
            report.add("fail", area, f"{said}, a redirect, which the ping does not follow, "
                       "so the key goes to no other host",
                       f"set {gateway.URL} to the host that answers itself, or unset it")
        elif exc.code == 429 or exc.code >= 500:
            report.add("warn", area, f"{said}; the gateway is busy or failing, try again later")
        else:
            report.add("fail", area, said, f"check {gateway.URL} if it names a proxy")
        return
    except http.client.InvalidURL:
        # nothing was sent; the exception's text may quote the URL
        report.add("fail", area, f"GET {shown} was not sent: urllib cannot use the URL",
                   f"check {gateway.URL}")
        return
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        report.add("warn", area, f"GET {shown} got no answer: {str(reason)[:120]}")
        return
    except http.client.HTTPException as exc:
        # a garbled status line or a cut-off body: neither an OSError nor a ValueError
        report.add("warn", area, f"GET {shown} got no answer it could read "
                   f"({type(exc).__name__})")
        return
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        report.add("warn", area, f"GET {shown} answered with no JSON")
        return
    if not isinstance(body, dict) or body.get("balance") is None \
            or body.get("total_used") is None:
        report.add("warn", area, f"GET {shown} answered without balance and total_used")
        return
    credits = {name: str(body[name])[:32] for name in ("balance", "total_used")}
    report.metrics["gateway_credits"] = credits
    report.add("ok", area, f"credits: balance {credits['balance']} USD, total_used "
               f"{credits['total_used']} USD (team-wide, not this run's)")


def _error_type(exc: urllib.error.HTTPError) -> str:
    """The `error.type` in a refused ping's JSON body, else ""."""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    kind = error.get("type") if isinstance(error, dict) else None
    return kind[:60] if isinstance(kind, str) else ""


def check_gateway_engine(report: Report) -> None:
    """The opt-in gateway engine's prerequisites (gateway_engine.py,
    AUTO-124), shown only on a checkout where gateway/ was installed: Node
    22 or later, the pinned `ai`, and whether AI_GATEWAY_API_KEY is set,
    by name only. The key row is left out when YT_GATEWAY is set, since
    the claude CLI's route is then checked with its key."""
    import gateway_engine
    from . import gateway
    if not (gateway_engine.PROGRAM_DIR / "node_modules").is_dir():
        return
    area = "gateway engine"
    exe = gateway_engine.node_executable(dict(os.environ))
    node = shutil.which(exe)
    major = gateway_engine.node_major(node) if node else None
    if not node:
        report.add("fail", area, f"{exe} is not on PATH; a stage that opts into the "
                   "gateway engine cannot run", f"install Node {gateway_engine.MIN_NODE} or "
                   f"later, or set ${gateway_engine.NODE_ENV}")
    elif major is None or major < gateway_engine.MIN_NODE:
        report.add("fail", area, f"{node} is Node {gateway_engine.node_version(node) or '?'}; "
                   f"the gateway engine needs {gateway_engine.MIN_NODE} or later",
                   f"install Node {gateway_engine.MIN_NODE} or later")
    else:
        report.add("ok", area, f"node at {node}, {gateway_engine.node_version(node)}")
    installed = gateway_engine.installed_ai_version()
    if installed == gateway_engine.AI_VERSION:
        report.add("ok", area, f"ai {installed} installed in gateway/node_modules")
    else:
        report.add("fail", area, gateway_engine.not_installed(installed),
                   gateway_engine.install_hint())
    if gateway.active():
        return
    if (os.environ.get(gateway.KEY) or "").strip():
        report.add("ok", area, f"{gateway.KEY} set")
    else:
        report.add("warn", area, f"{gateway.KEY} not set; a stage that opts into the "
                   "gateway engine is refused", f"export {gateway.KEY}")


def retention_baseline(report: Report, work: Path) -> None:
    """How many published films the channel has measured (`yt retention`,
    AUTO-101). Fewer than retention.BASELINE_FILMS is no baseline of the
    channel's own (judging 1), so every retention finding carries
    `sanity_bound` until there are that many. Said only once a film is
    measured: before that the evidence loop has nothing to say."""
    base = retention.channel_baseline(work)
    if not base["films"]:
        return
    n, need = base["films"], retention.BASELINE_FILMS
    report.metrics["retention_films"] = n
    said = (f"channel retention baseline: {n} published film(s) measured "
            f"({', '.join(base['slugs'])})")
    if n < need:
        report.add("note", "retention", f"{said}, under {need}: every retention finding "
                   "carries sanity_bound until the channel has its own baseline (judging 1, "
                   "judging 6)", "run `yt retention` on each film once it is published")
    else:
        report.add("note", "retention", f"{said}: the median Intro of the last {need} of "
                   "similar length is the baseline; findings that lean on a constant or a "
                   "house reading still carry sanity_bound")


#: The story review's verdicts (AUTO-93), as `yt script --review` appends them.
REVIEW_LOG = "review-log.jsonl"


def review_log(report: Report, work: Path) -> None:
    """How many story-review verdicts work/review-log.jsonl holds and how
    many a person has labelled with a verdict (narrative.labelled). It is
    the calibration set's raw material (judging 9: --gate waits for
    narrative.GATE_LABELLED labelled verdicts), and work/ is disposable, so
    the note says to copy it out."""
    path = Path(work) / REVIEW_LOG
    if not path.is_file():
        return
    log = narrative.read_review_log(path)
    agreed, labelled = narrative.agreement(log)
    unreadable = narrative.unreadable_labels(log)
    report.metrics["review_log"] = {"verdicts": len(log), "labelled": labelled,
                                    "agreed": agreed, "unreadable": unreadable}
    said = f"{len(log)} verdict(s) in {path}, {labelled} labelled by a person"
    if labelled:
        said += f", the reviewer agreed with {agreed}"
    if unreadable:
        # a label in other words is not dropped without a word
        said += f" ({unreadable} labelled with a word that is not a verdict)"
    report.add("note", "review log", said + "; it is evidence, and work/ is disposable",
               f"copy {path} out of work/ before clearing it; label a row by reading "
               "its script and setting `label` to reject, consider or recommend")


# --------------------------------------------------------------------------- #
# one video
# --------------------------------------------------------------------------- #

def check_hosts(report: Report, hosts: list, health: dict | None = None) -> None:
    """One finding per render host, and the routing each usable one gets, from
    the live reading (`health`: addr -> render.Health) when there is one. A host
    that takes a TCP connection on the ssh port but does not answer ssh is a
    hung or half-started sshd, not an unplugged machine: it is named as such."""
    health = health or {}
    usable = [h for h in hosts if h.usable]
    # the routing remote_host takes: a host whose live reading is gone is passed over
    answering = [h for h in usable if h.addr not in health or health[h.addr].alive]
    big = render.pick_host(answering, "encode", health)
    small = render.pick_host(answering, "still", health)
    for h in hosts:
        if h.usable:
            roles = []
            if h is big:
                roles.append("encode, audio")
            if h is small:
                roles.append("stills, frames, validate")
            disk = f", {h.disk_free_gb} GB free" if h.disk_free_gb else ""
            now = health.get(h.addr)
            live = ""
            if now is not None and now.alive:
                # 0 MB used is a reading (an idle GPU), so it is printed too
                live = f", load {now.load1:g}"
                if h.gpu_mem_mb:
                    live += f", GPU {now.gpu_used_mb}/{h.gpu_mem_mb} MB used"
            report.add("ok", "engine", f"{h.version} on {h.addr} ({h.ram_gb} GB, "
                       f"{h.cores} cores, {h.gpu or 'no gpu'}{disk}{live}) -> {'; '.join(roles) or 'slices'}")
            if now is not None and not now.alive:
                report.add("warn", "engine", f"{h.addr} answered the probe but not ssh now "
                           f"({_clip(now.error, 80) or 'no answer'}); jobs pass it over for "
                           f"{render.ALIVE_TTL:g} s",
                           "check sshd there, or take it out of RS_RENDER_HOSTS")
            busy = render.saturated(h, now)
            if busy:
                report.add("warn", "engine", f"{h.addr} is saturated ({busy}); stills, frames and "
                           f"validation go to {small.addr if small is not h else 'it anyway'}",
                           "wait for the load there to drop, or take it out of RS_RENDER_HOSTS")
            if 0 < h.disk_free_gb < LOW_DISK_GB:
                report.add("warn", "engine", f"{h.addr} has {h.disk_free_gb} GB free where its "
                           f"mirrors and jobs live (under {LOW_DISK_GB}); a long encode can fill it",
                           f"clean {render.REMOTE_ROOT} there, or `yt render --title X "
                           "--prune-remote` for a finished project")
            if not h.ffprobe:
                if h is big:
                    report.add("warn", "engine", f"no ffprobe on {h.addr}, the host an encode and "
                               "the gate go to: `yt gate` cannot run there",
                               "install ffmpeg on that host, or `yt gate --host` one that has it")
                else:
                    report.add("note", "engine", f"no ffprobe on {h.addr}; the gate runs on "
                               f"{big.addr if big else 'the encode host'}, not there")
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


def scene_digest(path: Path) -> str:
    """The sha256 of a scene document's bytes; empty when it cannot be read.
    `yt qa` records the same digest for the scene it judged (qa.scene_digest)."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _cmd(stage: str, project: workspace.Project, *extra: str) -> str:
    return " ".join(["yt", stage, "--project", str(project.root), *extra])


def _houses_of(record, depth: int = 2) -> list[dict]:
    """Every house digest a manifest records: its own `house`, and those of
    the reports it carries (`engine`, `director`, `engine.review`), since one
    manifest can hold the reports of more than one engine."""
    if not isinstance(record, dict):
        return []
    found = [record["house"]] if (isinstance(record.get("house"), dict)
                                  and "sha256" in record["house"]) else []
    for key, value in record.items() if depth else ():
        if key != "house":
            found += _houses_of(value, depth - 1)
    return found


def _stale(decided: dict, now: house.House) -> tuple[str, str] | None:
    """(was, is) when a recorded digest is not what loads now. A digest that
    names its stage is compared by the block that stage is handed, which is
    what its cache keys on; one from before stages were named, by the whole
    house."""
    if decided.get("stage") and "block_sha256" in decided:
        was, current = decided["block_sha256"], now.digest(decided["stage"])["block_sha256"]
    else:
        was, current = decided.get("sha256", ""), now.sha256
    return None if was == current else (was or "none", current or "none")


def check_house(project: workspace.Project, report: Report,
                work: Path | None = None) -> None:
    """Warn for each manifest decided under another house than the one that
    loads now (the shipped default, the channel's and the project's own).
    `work` is the directory the stages read `house.md` from (`--work`); with
    none, the project's parent, as `workspace.projects` lays it out. A run
    that named `--house` or `--no-house` shows up here too, which is the
    truth: its decisions are not what the next plain run would ask."""
    now = house.load(work if work is not None else project.root.parent, project.root)
    for name in HOUSE_MANIFESTS:
        path = project.root / name
        if not path.is_file():
            continue
        for decided in _houses_of(read_json(path, warn=False)):
            stale = _stale(decided, now)
            if stale is None:
                continue
            stage = decided.get("stage")
            report.add("warn", "house", f"{name} was decided under a different house "
                       f"digest ({stale[0]}; now {stale[1]}): "
                       + (f"{stage} asks its cached decisions again on its next run, "
                          "because `## all` or its own section changed" if stage else
                          "its decisions are asked again where its own section or "
                          "`## all` changed"))
            break


#: The verdict `yt qa` writes into qa.json when the bar is met (qa.READY;
#: kept here so doctor stays model-free, and held equal by a test).
READY_TO_PUBLISH = "READY TO PUBLISH"
#: The verdict `yt qa` writes when fewer than two of its scoring samples
#: answered (qa.NOT_EVIDENCED, AUTO-107): nothing to route, only to score
#: again. Held equal by a test, like READY_TO_PUBLISH.
QA_NOT_EVIDENCED = "not_evidenced"

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
    # A gate or a QA pass judges the video, but QA renders its frames from
    # scene.xml: both are stale against the newer of the two (improve's rule).
    scene = project.path("scene")
    scene_at = _mtime(scene) if scene.exists() else 0.0
    judged_after = max(rendered_at, scene_at)
    if rendered_at and scene_at > rendered_at:
        missing.append(("video.mp4 predates scene.xml: the scene was rebuilt after the "
                        "render, so the video shows an older cut",
                        _cmd("render", project, "--quality", "final", "--strict")))
    gate_file = project.root / "gate.json"
    gate = read_json(gate_file, warn=False) if gate_file.exists() else None
    if not gate:
        missing.append(("no delivery gate has run on this render", _cmd("gate", project)))
    elif gate.get("mode") == "frames":
        missing.append(("the last gate checked decoded frames only; delivery needs "
                        "the technical gate", _cmd("gate", project)))
    elif _mtime(gate_file) < judged_after:
        missing.append(("the gate predates the render it should judge", _cmd("gate", project)))
    elif gate.get("exit") != 0 or (gate.get("triage") or {}).get("verdict") != "deliverable":
        blocking = len((gate.get("triage") or {}).get("blocking") or [])
        missing.append((f"the gate found it not deliverable ({blocking} blocking)",
                        _cmd("gate", project)))

    qa_file = project.root / "qa.json"
    qa = read_json(qa_file, warn=False) if qa_file.exists() else None
    if not qa:
        missing.append(("no QA pass has scored this render", _cmd("qa", project)))
    elif qa.get("scene_sha256") and scene_at and qa["scene_sha256"] != scene_digest(scene):
        missing.append((f"QA {qa.get('pass', '')} judged a different scene.xml than the "
                        "one now built", _cmd("qa", project)))
    elif _mtime(qa_file) < (rendered_at if qa.get("scene_sha256") else judged_after):
        missing.append((f"QA {qa.get('pass', '')} predates the render it should judge",
                        _cmd("qa", project)))
    elif qa.get("verdict") == QA_NOT_EVIDENCED:
        missing.append((f"QA {qa.get('pass', '')} is not evidenced (fewer than two "
                        "scoring samples answered)", _cmd("qa", project)))
    elif qa.get("verdict") != READY_TO_PUBLISH:
        missing.append((f"QA {qa.get('pass', '')} says {qa.get('verdict')} at "
                        f"{qa.get('overall', '?')}", _cmd("qa", project)))
    return missing


def check_renders(project: workspace.Project, report: Report) -> None:
    """List the render jobs that did not render (AUTO-32): every render*.json and every
    kit-frame report under .cache/reports/ that carries a status. A job that timed out,
    lacked an input or failed leaves one; this is where an operator reads it, not from
    a hung author or a missing frame."""
    paths = sorted(project.root.glob("render*.json")) + sorted(
        (project.root / ".cache" / "reports").glob("*.json"))
    for path in paths:
        rep = read_json(path, warn=False)
        if not isinstance(rep, dict) or rep.get("status") not in render.STATUSES:
            continue
        status = rep["status"]
        if status == "rendered":
            continue
        job = rep.get("job") or path.stem
        spent = f" after {float(rep.get('seconds') or 0):g} s"
        detail = (f"timed out{spent}" if status == "timeout"
                  else "missing " + ", ".join(rep.get("missing_inputs") or []) if status == "blocked-inputs"
                  else f"failed{spent}")
        if rep.get("outcome_unknown"):
            detail += " (outcome unknown: the host never reported)"
        note = str(rep.get("message") or "")[:100]
        where = path.relative_to(project.root).as_posix()
        report.add("warn", "render-jobs", f"{job} {status}: {detail}" + (f" -- {note}" if note else "")
                   + f" [{where}]",
                   {"timeout": "lower the document's cost drivers or render it in windows",
                    "blocked-inputs": "produce the missing files, then " + _cmd("build", project),
                    }.get(status, "read the report, then re-run the job"))


#: The counters gateway_engine puts on a by_model row, one per way it found
#: the call's cost (AUTO-124).
GATEWAY_COST_COUNTERS = ("cost_gateway_calls", "cost_generation_calls",
                         "cost_table_calls", "cost_unpriced_calls")


def _priced(model: str, row: dict | None = None) -> bool:
    """Whether `model`'s calls were priced. A row the gateway engine booked
    says so itself: priced when the gateway (its metadata or its generation
    record) gave a cost and no call went unpriced. Otherwise, whether an
    engine has a row for `model`. A record does not say which CLI wrote it,
    and under `YT_ENGINE=codex` `by_model` is keyed by Codex's own ids,
    priced from its table and `$CODEX_PRICES`; the modules are imported
    here, as `harness.use_engine` does, not at the top."""
    if isinstance(row, dict) and any(k in row for k in GATEWAY_COST_COUNTERS):
        counted = {k: row.get(k) or 0 for k in GATEWAY_COST_COUNTERS}
        if (counted["cost_gateway_calls"] + counted["cost_generation_calls"] > 0
                and counted["cost_unpriced_calls"] == 0):
            return True
        if counted["cost_unpriced_calls"]:
            return False
        import gateway_engine
        if gateway_engine.price_for(model) is not None:
            return True
    if spend.price_for(model) is not None:
        return True
    import codex_engine
    return codex_engine.price_for(model) is not None


def check_spend(project: workspace.Project, report: Report) -> None:
    """Warn when a record bills a model nobody priced (AUTO-73): a stage's
    `usage.by_model` (or a harness stage's `engine.by_model`) names the ids
    the CLI reported, and a Claude call on an id with no row in `spend.PRICES`
    or `$CLAUDE_PRICES` is booked at $0 when it times out (a Codex call on one
    is recorded without a cost). keyframe.json keeps its two harness reports
    one level down (`engine.elements`, `engine.review`), so an account
    without a breakdown is opened once. A row with `cost_cli_calls` was
    booked through the gateway at the claude CLI's own figure, not the price
    table (AUTO-121), and is warned about on its own line."""
    unpriced: dict[str, list[str]] = {}
    estimated: dict[str, list[str]] = {}
    for path in sorted(project.root.glob("*.json")):
        rec = read_json(path, warn=False)
        if not isinstance(rec, dict):
            continue
        for key in ("usage", "engine"):
            account = rec.get(key)
            if not isinstance(account, dict):
                continue
            accounts = [account] if "by_model" in account else [
                v for v in account.values() if isinstance(v, dict)]
            for model, row in ((m, r) for a in accounts
                               for m, r in (a.get("by_model") or {}).items()):
                if isinstance(row, dict) and row.get("cost_cli_calls"):
                    # Through the gateway with nothing the table could price
                    # it by (AUTO-121): booked at the claude CLI's own figure.
                    estimated.setdefault(str(model), []).append(path.name)
                elif not _priced(str(model), row):
                    unpriced.setdefault(str(model), []).append(path.name)
    for model, names in sorted(unpriced.items()):
        report.add("warn", "spend", f"{model} has no price; {', '.join(sorted(set(names)))} "
                   "bill calls on it; a Claude call on it that times out is booked at $0, "
                   "a Codex call on it is recorded without a cost",
                   "add its row to ytstudio/spend.PRICES or $CLAUDE_PRICES (a Claude id), "
                   "codex_engine.PRICES or $CODEX_PRICES (a Codex one)")
    for model, names in sorted(estimated.items()):
        report.add("warn", "spend", f"{model}: {', '.join(sorted(set(names)))} book calls "
                   "made through the gateway at the claude CLI's own figure (cost_cli_calls), "
                   "not the price table, so the record may differ from the gateway's bill",
                   "add the model's row to ytstudio/spend.PRICES or $CLAUDE_PRICES if it has "
                   "none; a call whose result carried no tokens, a prompt above the model's "
                   "tier (Haiku 5.5 over 100K tokens) or a server tool other than web search "
                   "is booked this way whatever the table says")


def check_project(project: workspace.Project, work: Path | None = None) -> Report:
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
        # The narration rules' findings (AUTO-94), in sdkkit's prefixes: an
        # `error:` stopped the stage (or was recorded with --report-only), a
        # `warning:` did not, and a rule the film's language has no evidence
        # for is a note. The stage's own `note:` lines were printed when it ran.
        for finding in script.get("findings") or []:
            finding = str(finding)
            level, _, text = finding.partition(": ")
            if level == "error" and text.startswith("story review:"):
                # AUTO-93: the story review rejected the script. Lines are
                # not where a reject is fixed: the plan is, read again.
                report.add("fail", "script", _clip(text),
                           _cmd("script", project, "--fresh", "--review", "--notes",
                                '"<what the Story review in script.md asks>"')
                           + " (the verdict stops voice only under --gate)")
            elif level == "error":
                report.add("fail", "script", _clip(text),
                           _cmd("script", project, "--notes",
                                '"<how to clear the rule>"')
                           + " (or --report-only to record it and go on)")
            elif level == "warning":
                report.add("warn", "script", _clip(text),
                           "read the Findings section of script.md")
            elif level == "not_evidenced":
                report.add("note", "script", f"not checked: {text}")
        if script.get("gate"):
            # AUTO-97: --gate trusts the reviewer, which is a prior until a
            # person has labelled enough of its verdicts (judging 9).
            log = narrative.read_review_log(Path(work or project.root.parent) / REVIEW_LOG)
            count = narrative.labelled(log)
            if count < narrative.GATE_LABELLED:
                report.add("warn", "script",
                           f"gate on with {count} labelled verdicts "
                           f"({narrative.GATE_LABELLED} needed): this script gated voice "
                           "on a story review not yet calibrated",
                           "label rows of work/review-log.jsonl (reject, consider or "
                           "recommend), run `python -m ytstudio.narrative calibrate "
                           "tests/fixtures/narrative`, and until both hold drop --gate")
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
        elif look["state"] == "failed":
            why = keyframe.approvable(keyframe.load_record(project.root)) or "nothing was rendered"
            report.add("warn", "keyframe", f"the look pass failed and cannot be approved: {why}",
                       _cmd("keyframe", project))
        elif look["state"] == "rejected":
            report.add("warn", "keyframe", f"the keyframe was rejected: {_clip(look['notes'])}",
                       _cmd("keyframe", project))
        elif look["state"] == "stale":
            report.add("warn", "keyframe", f"{look['document'] or 'the keyframe document'} "
                       "changed after it was approved; the look on disk is not the approved one",
                       _cmd("keyframe", project, "--approve"))
        else:
            report.add("ok", "keyframe", f"approved {look['at']} · {shown}")
    if vfx:
        # A look taken from the film enters an existing animation with
        # --relook; --fresh would replan and author its section again too.
        section_look = keyframe.load_record(project.root).get("source") == "section"
        if look["state"] != "approved":
            first = (_cmd("keyframe", project, "--approve")
                     if look["state"] == "pending" and section_look else _cmd("keyframe", project))
            report.add("warn", "vfx", "the animation was authored without an approved "
                       "keyframe; no look was locked for it to inherit",
                       first + ", then " + (_cmd("vfx", project, "--relook") if section_look
                                            else _cmd("vfx", project, "--fresh")))
        elif section_look:
            if not keyframe.film_carries_look(project.root, vfx):
                report.add("warn", "vfx", "the animation does not hold the approved look: "
                           "its section is another authoring", _cmd("vfx", project, "--relook"))
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
        # The screenplay's own errors ride in build.json for qa (`error:
        # script: ...`); the script area already fails them with the command
        # that clears them, and rebuilding would only carry them over again.
        for error in errors:
            if not str(error).startswith("error: script: "):
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
        if qa_done.get("verdict") == QA_NOT_EVIDENCED:
            # Nothing was scored, so nothing is routed: QA is run again.
            report.add("note", "delivery", f"QA {qa_done.get('pass', '')} is not evidenced "
                       "(fewer than two scoring samples answered): score the video again",
                       _cmd("qa", project))
        elif qa_done.get("verdict") and qa_done.get("verdict") != READY_TO_PUBLISH:
            # QA names an owner per finding; the loop routes them back.
            report.add("note", "delivery", f"QA {qa_done.get('pass', '')} is not ready: "
                       "route its findings to their stages and re-run them",
                       _cmd("improve", project, "--dry-run"))
        if not missing:
            report.add("ok", "delivery", f"{READY_TO_PUBLISH}: a clean final render, "
                       f"gate deliverable, QA {qa_done.get('pass', '')} at "
                       f"{qa_done.get('overall', '?')}")

    check_renders(project, report)
    check_spend(project, report)

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
    check_house(project, report, work)

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
            extra = {"script": ("--polish", "--verify", "--review"), "sound": ("--review",),
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
    p.add_argument("--gateway-ping", action="store_true", dest="gateway_ping",
                   help="also ask Vercel AI Gateway for the team's credit balance "
                        "(one GET, no model call; needs AI_GATEWAY_API_KEY)")
    p.set_defaults(run=run)
    return p


def run(args) -> int:
    work = workspace.work_dir(args)
    reports = [check_environment(work, ping=getattr(args, "gateway_ping", False))]
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
            reports.append(check_project(project, work))

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
