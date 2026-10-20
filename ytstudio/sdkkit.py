"""The kit an authoring agent composes with: it writes programs against the
sr-core SDK, and the program, not hand-written XML, is the source of a scene.

The owner's rule: the creative part is a model coding with `sr_core.scene`
under the harness's rules. A program can do what a hand-written document
cannot -- detail made by procedure, motion computed from a function, a
revision that changes one parameter instead of ten thousand keys -- and it is
a source that diffs. This module owns only the workspace for that: what the
agent reads, the helper its programs import, and the build that runs a
program, checks what it wrote and records where the document came from. It
chooses no look, no value and no construct.

    kit = install(ws_dir, frame=(1920, 1080), fps=30)
    result = build(ws_dir / "compose.py")      # runs it twice, checks, records
    png, findings = frame(ws_dir / "world.scene.xml", 2.0)

What lands in the workspace:

    srkit.py              the helper a program imports (`S`, `FRAME`, `save`)
    kit.py                the commands: describe, enum, elements, build, check, frame
    kit/SDK-GUIDE.md      sr-core's own guide to the SDK, for people and agents
    kit/ENGINE.md         what the engine renders, approximates or only reports,
                          and what the schema says each renderer ignores
    kit/examples/*.py     programs that built, validated and rendered on this engine
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .scene_sdk import sdk

KIT_DIR = "kit"
EXAMPLES_SRC = Path(__file__).resolve().parent / "kit" / "examples"
AUTOMATION_ROOT = Path(__file__).resolve().parent.parent

#: Where the engine's source checkout is, for its capability manifest when no
#: host answers. Reading a JSON file is not running the engine.
ENGINE_SRC = Path(os.environ.get("SR_ENGINE_SRC") or Path.home() / "src" / "scene-render")

#: Phrases by which the schema's own documentation says an attribute depends
#: on the renderer. The schema is the source; this only finds the sentences.
RENDERER_WORDS = re.compile(r"path[- ]trac|pathtrace|rasteri[sz]ed renderer|raster renderer|"
                            r"raster only|rasterized output|OCIO|ocioConfig|screen-space", re.I)

PROGRAM_TIMEOUT = 300.0

SRKIT = '''"""srkit -- what a composing program imports.

    from srkit import S, FRAME, save

    scene = S.Scene(version="auto")
    scene.add_project(width=FRAME["width"], height=FRAME["height"], fps=FRAME["fps"], duration=4)
    ...
    save(scene, "world.scene.xml")

`S` is sr_core.scene, the SDK generated from the schema: every element is a
class, every allowed child an `add_<name>` factory, and a wrong name or value
raises at once with the names that are allowed (`S.describe("camera")` tells
you the rest). `save` checks the whole document, writes it and records it for
`python3 kit.py build`, which runs the program twice and refuses a document
that changes between runs: seed every random choice.
"""
import hashlib, json, os, sys
sys.path.insert(0, {sr_root!r})
from sr_core import scene as S

_here = os.path.dirname(os.path.abspath(__file__))
try:
    FRAME = json.load(open(os.path.join(_here, "frame.json")))
except OSError:
    FRAME = {{"width": 1920, "height": 1080, "fps": 30}}

_sdk_read = S.read


def _read(source, *args, **kwargs):
    """S.read, recorded for the build: reading another document for its values is
    fine, but a document read and saved again is not composed by the program."""
    doc = _sdk_read(source, *args, **kwargs)
    record = os.environ.get("SRKIT_RECORD")
    if record:
        try:
            canonical = hashlib.sha256(doc.to_bytes(layout="pretty")).hexdigest()
        except Exception:
            canonical = ""
        what = "<bytes>" if isinstance(source, (bytes, bytearray)) else str(source)[:200]
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{"read": what, "canonical": canonical}}) + "\\n")
    return doc


S.read = _read
try:
    import sr_core.scene.io as _io
    _io.read = _read
except Exception:
    pass


def save(scene, path):
    """Check the document, write it as pretty XML, record it for the build."""
    findings = [str(f) for f in S.check(scene)]
    data = scene.to_bytes(layout="pretty")
    path = os.path.abspath(path)
    with open(path, "wb") as fh:
        fh.write(data)
    record = os.environ.get("SRKIT_RECORD")
    if record:
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                                 "findings": findings}}) + "\\n")
    for f in findings:
        print(f, file=sys.stderr)
    print(f"saved {{os.path.relpath(path)}} · {{len(data)}} bytes · {{len(findings)}} finding(s)")
    return path
'''

KIT_CLI = '''#!/usr/bin/env python3
"""kit -- compose scene-render documents as programs written with the sr-core SDK.

    python3 kit.py elements                 every element the schema knows
    python3 kit.py describe <element>       attributes, types, enums, children
    python3 kit.py enum <element> <attr>    the values one attribute allows
    python3 kit.py build <program.py>       run it twice, check every document it saved
                                            (the SDK, then the engine's validation),
                                            refuse one that differs between runs, and
                                            write <doc>.provenance.json beside it
    python3 kit.py check <file.scene.xml>   check one document the same way
    python3 kit.py frame <file.scene.xml> --at SECONDS
                                            render one frame on the render host into
                                            frames/kit/ and run the frame checks -- then
                                            Read the PNG: validation cannot see it
"""
import sys
sys.path.insert(0, {automation_root!r})
from ytstudio import sdkkit
sys.exit(sdkkit.main(sys.argv[1:]))
'''


# --------------------------------------------------------------------------- #
# what the agent reads
# --------------------------------------------------------------------------- #

def sr_root() -> Path:
    """The sr-core checkout or install the SDK was loaded from."""
    return Path(sdk().__file__).resolve().parents[2]


def guide_text() -> str:
    """sr-core's guide to the SDK, as its maintainers wrote it for agents."""
    path = sr_root() / "docs" / "scene-sdk-guide.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ("# Scene SDK guide\n\nThe guide was not found beside the SDK "
                f"({path}). Ask the SDK instead: `python3 kit.py describe <element>`.\n")


def renderer_notes() -> list[dict]:
    """Every attribute whose schema documentation says it depends on the
    renderer: (element, attribute, the sentence). Read from `describe_all()`."""
    out = []
    for element in sdk().describe_all().get("elements", []):
        names = element.get("xml_names") or [element.get("class_name")]
        for attr in element.get("attributes") or []:
            doc = " ".join(str(attr.get("doc") or "").split())
            if RENDERER_WORDS.search(doc):
                out.append({"element": str(names[0]), "attribute": attr.get("xml_name"),
                            "doc": doc})
    return out


def engine_manifest(host: str = "") -> dict:
    """The engine's capability manifest, from the render host when it answers,
    else from the engine's source checkout; `source` says which."""
    if host:
        try:
            from .assess import engine_facts
            facts = engine_facts(host)
            caps = facts.get("capabilities")
            if isinstance(caps, dict) and caps.get("entries") is not None:
                return {"source": f"scene-render capabilities on {host}", "manifest": caps,
                        "gpus": facts.get("gpus", ""), "memory_and_cores":
                        facts.get("memory_and_cores", "")}
        except Exception:      # an unreachable host falls back to the checkout
            pass
    path = ENGINE_SRC / "capabilities.json"
    try:
        return {"source": f"{path} (the engine checkout; a host's build may differ)",
                "manifest": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {"source": "none: no host answered and no checkout was found", "manifest": {}}


def engine_text(engine: dict, notes: list[dict], measured: list[dict] | None = None) -> str:
    """ENGINE.md: the manifest, the renderer notes and anything measured."""
    caps = engine.get("manifest") or {}
    eng = caps.get("engine") or {}
    lines = ["# What this engine does with a document", "",
             f"Engine: {eng.get('name', '?')} {eng.get('version', '?')} · schema "
             f"{caps.get('schema', '?')} · from {engine.get('source', '?')}", "",
             "A document that validates is not a picture. The engine renders most "
             "constructs exactly; the ones below it approximates, only reports, or "
             "applies with one renderer and not the other. Choose the renderer "
             "(`camera/@renderer`) knowing which attributes it will ignore.", "",
             "## The engine's capability manifest", ""]
    entries = caps.get("entries") or []
    if entries:
        lines.append("Anything not listed here is claimed exact.")
        lines.append("")
        for row in entries:
            when = f" (when {', '.join(row.get('when') or [])})" if row.get("when") else ""
            lines.append(f"- `{row.get('construct')}` -- {row.get('status')}{when}: "
                         f"{row.get('note', '')}")
    else:
        lines.append("The manifest could not be read; treat every construct as unproven "
                     "until a frame shows it.")
    lines += ["", "## What a frame costs", "",
              "Every frame `kit.py frame` renders reports how long it took and with which "
              "settings (`note: rendered in ...`). A path-traced frame's time grows with "
              "`pathSamples` (roughly in proportion) and with lights and bounces; a raster "
              f"frame takes seconds. A still that runs past {STILL_DEADLINE} s is abandoned "
              "and counts as not rendered, and every frame you wait for is time your session "
              "does not get back: time one frame before you commit to a setting, and judge "
              "composition at a setting that renders quickly.", "",
              "## What the schema says each renderer applies", ""]
    for n in notes:
        lines.append(f"- `{n['element']}/@{n['attribute']}`: {n['doc']}")
    if measured:
        lines += ["", "## Measured on this engine by the probe suite", ""]
        for m in measured:
            lines.append(f"- {m.get('probe')}: {m.get('score')} -- {m.get('summary', '')}"
                         + (f" [one frame: {m['render']}]" if m.get("render") else ""))
    if engine.get("gpus"):
        lines += ["", "## The render host", "", engine["gpus"],
                  str(engine.get("memory_and_cores", ""))]
    return "\n".join(lines) + "\n"


def example_files() -> list[Path]:
    return sorted(EXAMPLES_SRC.glob("*.py"))


def examples_index() -> str:
    """kit/examples/README.md: each example's first docstring line."""
    lines = ["# Example programs", "",
             "Each one built with `python3 kit.py build`, validated by the engine and "
             "rendered on it. They show how a construct is written with the SDK and how a "
             "program does what a document cannot (procedure, computed motion, parameters). "
             "Their values are placeholders: nothing in them is a look to copy.", ""]
    for path in example_files():
        doc = _docstring(path)
        lines.append(f"- `{path.name}` -- {doc.splitlines()[0] if doc else ''}")
    return "\n".join(lines) + "\n"


def _docstring(path: Path) -> str:
    import ast
    try:
        return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except SyntaxError:
        return ""


def install(directory: Path, *, frame: tuple[int, int] | None = None, fps: int = 30,
            host: str = "", measured: list[dict] | None = None) -> dict:
    """Write the kit into `directory`. `frame` writes frame.json (a stage that
    already writes one passes None). Returns what was installed."""
    directory = Path(directory)
    kit = directory / KIT_DIR
    (kit / "examples").mkdir(parents=True, exist_ok=True)
    (directory / "srkit.py").write_text(SRKIT.format(sr_root=str(sr_root())), encoding="utf-8")
    (directory / "kit.py").write_text(KIT_CLI.format(automation_root=str(AUTOMATION_ROOT)),
                                      encoding="utf-8")
    if frame is not None:
        (directory / "frame.json").write_text(
            json.dumps({"width": frame[0], "height": frame[1], "fps": fps}), encoding="utf-8")
    (kit / "SDK-GUIDE.md").write_text(guide_text(), encoding="utf-8")
    engine = engine_manifest(host)
    notes = renderer_notes()
    (kit / "ENGINE.md").write_text(engine_text(engine, notes, measured), encoding="utf-8")
    for path in example_files():
        (kit / "examples" / path.name).write_text(path.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
    (kit / "examples" / "README.md").write_text(examples_index(), encoding="utf-8")
    return {"dir": str(directory), "engine_source": engine.get("source"),
            "renderer_notes": len(notes), "examples": [p.name for p in example_files()]}


#: The system prompt of an agent that composes with the kit, so every stage
#: that starts one asks for the same discipline.
AUTHOR_SYSTEM = (
    "You compose scene-render scenes by writing Python programs with the sr-core "
    "SDK. The program is the source and the document it saves is its output: you "
    "never hand-write or hand-edit the XML. You ask the SDK before you guess "
    "(`python3 kit.py describe <element>`), you build with `python3 kit.py build "
    "<program.py>` until it reports 0 errors and a deterministic document, and, "
    "because validation cannot see a picture, you then render frames (`python3 "
    "kit.py frame <doc> --at <seconds>`) and look at every PNG with the Read tool: "
    "a black, flat, floating or wrong frame means the work is not done. You use "
    "what a program can do -- loops, functions, parameters, seeded randomness -- and "
    "you never invent an asset file, a font or a value the schema does not accept."
)

#: The tools such an agent gets: its workspace's files and a shell for the kit.
AUTHOR_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

#: What a brief says about the kit, so every stage that installs it says the same.
BRIEF = """## How you compose: a program, not a document

You write a Python program with the sr-core SDK (`from srkit import S, FRAME, save`)
that builds the scene and saves it. The program is the source: a revision changes the
program, never the XML it wrote. Use what a program can do -- loops for detail,
functions for motion, parameters for what a review may ask you to change, a seeded
random generator where variation is wanted.

- `kit/SDK-GUIDE.md`: how the SDK builds, checks and reports; read it first.
- `kit/ENGINE.md`: what the engine approximates or ignores, per renderer. Pick the
  renderer for what the picture needs, knowing what it drops.
- `kit/examples/`: programs that rendered on this engine. Their values are placeholders.
- `python3 kit.py describe <element>` before writing an attribute you have not used.
- `python3 kit.py build <program.py>` until it reports 0 errors and a deterministic
  document; then `python3 kit.py frame <doc> --at <seconds>` and Read every PNG.
"""


# --------------------------------------------------------------------------- #
# the build
# --------------------------------------------------------------------------- #

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_program(program: Path, timeout: float) -> tuple[subprocess.CompletedProcess | None,
                                                         list[dict], str]:
    """Run once in a fresh interpreter; (process, records, error). A record is
    a document `save` wrote (`path`) or one the program read (`read`)."""
    fd, record = tempfile.mkstemp(prefix="srkit-", suffix=".jsonl")
    os.close(fd)
    env = dict(os.environ, SRKIT_RECORD=record, PYTHONHASHSEED="0")
    try:
        proc = subprocess.run([sys.executable, program.name], cwd=program.parent, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        Path(record).unlink(missing_ok=True)
        return None, [], f"the program ran longer than {timeout:.0f}s"
    try:
        rows = [json.loads(line) for line in
                Path(record).read_text(encoding="utf-8").splitlines() if line.strip()]
    finally:
        Path(record).unlink(missing_ok=True)
    error = ""
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout).strip()[-3000:] or f"exit code {proc.returncode}"
    elif not [r for r in rows if "path" in r]:
        error = "the program saved no document: call srkit.save(scene, path)"
    return proc, rows, error


#: Light types that can cast a shadow (`light/@castShadow` has no effect on the others).
SHADOW_LIGHTS = ("directional", "point", "spot", "rect-area", "disk-area", "sphere-area")


def rule_findings(data: bytes) -> list[str]:
    """The harness's rules on a document, beyond what the schema can say."""
    from xml.etree import ElementTree
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []
    local = lambda el: el.tag.rsplit("}", 1)[-1]           # noqa: E731
    nodes = list(root.iter())
    solids = [el for el in nodes if local(el) == "object3D"]
    casters = [el for el in nodes if local(el) == "light"
               and el.get("type") in SHADOW_LIGHTS]
    out = []
    if solids and casters and not any((el.get("castShadow") or "").strip() in ("true", "1")
                                      for el in casters):
        out.append("warning: rule:shadow: no light casts a shadow (light/@castShadow defaults "
                   "to false), so no object can be seen resting on anything; set castShadow "
                   "on the light that should ground the scene")
    return out


def check_document(path: Path, *, host: str = "", validate: bool = True) -> dict:
    """The SDK's checks, the harness's rules and the engine's validation."""
    data = Path(path).read_bytes()
    out: dict = {"path": str(path), "sha256": _sha(data), "sdk": [], "engine": None}
    try:
        doc = sdk().read(data)
        out["sdk"] = [str(f) for f in doc.check()] + rule_findings(data)
        out["version"] = str(doc.version)
    except Exception as exc:
        out["sdk"] = [f"error: the document does not parse: {exc}"]
    if validate:
        from . import render
        engine = render.engine_validate(data, host=host)
        if engine and any(m in f for f in engine for m in UNREACHABLE):
            # The host did not answer: the document is not judged, not invalid.
            out["sdk"].append("note: the engine's validation could not run (render host "
                              "unreachable); only the SDK's checks ran")
            engine = None
        out["engine"] = engine
    findings = out["sdk"] + (out["engine"] or [])
    out["errors"] = [f for f in findings if f.startswith("error")]
    return out


def build(program: Path, *, host: str = "", runs: int = 2, timeout: float = PROGRAM_TIMEOUT,
          validate: bool = True) -> dict:
    """Run a composing program `runs` times, check every document it saved,
    and write `<doc>.provenance.json` beside each. A document whose bytes
    differ between runs is an error: the program is the source only if it
    reproduces its output."""
    program = Path(program).resolve()
    result: dict = {"program": program.name, "ok": False, "documents": [],
                    "when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        source = program.read_bytes()
    except OSError as exc:
        result["error"] = f"cannot read the program: {exc}"
        return result
    result["program_sha256"] = _sha(source)
    runs_rows: list[dict[str, str]] = []
    reads: list[dict] = []
    for _ in range(max(1, runs)):
        proc, rows, error = _run_program(program, timeout)
        if error:
            result["error"] = error
            return result
        result["stdout"] = (proc.stdout or "").strip()[-2000:] if proc else ""
        saved: dict[str, str] = {}
        for r in rows:
            if "path" not in r:
                continue
            path = Path(r["path"])
            # What counts is the file the program left, not what save() saw.
            if not path.is_file():
                result["error"] = (f"the program saved {os.path.relpath(path, program.parent)} "
                                   "and then removed or renamed it")
                return result
            on_disk = _sha(path.read_bytes())
            if on_disk != r["sha256"]:
                result["error"] = (f"the program changed {os.path.relpath(path, program.parent)} "
                                   "after saving it; let save() write the final document")
                return result
            saved[r["path"]] = on_disk
        runs_rows.append(saved)
        reads = [r for r in rows if "read" in r]
    first = runs_rows[0]
    for saved in runs_rows[1:]:
        if saved != first:
            result["error"] = ("the program wrote different documents on two runs; seed every "
                               "random choice and do not read the clock")
            result["deterministic"] = False
            return result
    result["deterministic"] = True
    passthrough = [os.path.relpath(p, program.parent) for p, digest in first.items()
                   if any(r.get("canonical") == digest for r in reads)]
    if passthrough:
        result["error"] = (f"rule:program-passthrough: {', '.join(passthrough)} is a scene "
                           "document the program read and saved again; build the scene with "
                           "the SDK in the program (reading another document for its values "
                           "is fine)")
        return result
    S = sdk()
    for path, digest in first.items():
        checked = check_document(Path(path), host=host, validate=validate)
        checked["path"] = os.path.relpath(path, program.parent)
        checked["sdk"] += program_findings(reads)
        result["documents"].append(checked)
        provenance = {"document": checked["path"], "document_sha256": digest,
                      "program": program.name, "program_sha256": result["program_sha256"],
                      "deterministic": True, "runs": len(runs_rows),
                      "schema_release": getattr(S, "SCHEMA_RELEASE", ""),
                      "sdk_findings": len(checked["sdk"]),
                      "engine_validated": checked["engine"] is not None,
                      "errors": len(checked["errors"]), "when": result["when"]}
        Path(path).with_suffix("").with_suffix(".provenance.json").write_text(
            json.dumps(provenance, indent=1), encoding="utf-8")
    result["ok"] = not any(d["errors"] for d in result["documents"])
    return result


def program_findings(reads: list[dict]) -> list[str]:
    """The harness's rules on what a program did beyond saving: srkit records
    every scene document it read. Saving one again is refused by the build;
    reading one for its values is noted, so a reviewer can see it."""
    if not reads:
        return []
    names = ", ".join(sorted({str(r.get("read", "?")) for r in reads}))[:300]
    return [f"warning: rule:program-reads-document: the program read {names}; take values "
            "from another document if you must, but build the scene with the SDK here"]


def build_findings(program: Path, document: str) -> list[str]:
    """An authoring gate's view of the harness's own build of `program`: an
    error when there is no program, when it fails or does not reproduce its
    output, or when it did not save `document`; otherwise the harness's rules
    on the program and the document. The SDK's own checks are left to the
    gate's validation, which runs them on the document the build just wrote."""
    program = Path(program)
    if not program.is_file():
        return [f"error: rule:program: no `{program.name}`. Write the scene as a Python "
                f"program with the SDK (`from srkit import S, FRAME, save`) that saves "
                f"`{document}`, and build it with `python3 kit.py build {program.name}`; a "
                "hand-written document does not count as authored"]
    try:
        result = build(program, validate=False)
    except Exception as exc:          # an untrusted program must not crash the stage
        return [f"error: rule:program: `python3 kit.py build {program.name}` could not be "
                f"judged: {type(exc).__name__}: {str(exc)[:400]}"]
    if result.get("error"):
        return [f"error: rule:program: `python3 kit.py build {program.name}` failed: "
                f"{result['error'][:800]}"]
    saved = [d["path"] for d in result["documents"]]
    if document not in saved:
        return [f"error: rule:program: `{program.name}` saved {', '.join(saved) or 'nothing'}, "
                f"not `{document}`"]
    return [f for d in result["documents"] if d["path"] == document
            for f in d["sdk"] if "rule:" in f]


def provenance_of(doc: Path) -> dict | None:
    """The provenance a build wrote beside a document, if the document is
    still the one it built (same bytes)."""
    doc = Path(doc)
    try:
        record = json.loads(doc.with_suffix("").with_suffix(".provenance.json")
                            .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("document_sha256") != _sha(doc.read_bytes()):
        return None
    return record


#: What ssh says when a render host does not answer.
UNREACHABLE = ("timed out during banner exchange", "port 22 timed out", "Connection refused",
               "No route to host", "Connection timed out", "Could not resolve hostname")


#: The note `frame` adds to a rendered still: how long it took, where, and with
#: which renderer settings, so render cost is measured, never guessed.
RENDER_NOTE = "rendered in"

#: What a still that renders past this many seconds costs: it is abandoned.
STILL_DEADLINE = 600


def render_settings(text: str) -> str:
    """The renderer settings that decide a still's cost, as one phrase."""
    renderer = re.search(r'renderer="(\w+)"', text)
    samples = re.search(r'pathSamples="(\d+)"', text)
    bounces = re.search(r'maxBounces="(\d+)"', text)
    if renderer and renderer.group(1) == "pathtrace":
        return (f"pathtrace, {samples.group(1) if samples else 64} samples, "
                f"{bounces.group(1) if bounces else 4} bounces"
                + (", a medium" if "<medium" in text else ""))
    return "raster" + (", a medium (path traced at 4 samples)" if "<medium" in text else "")


#: A path-traced camera or a participating medium makes a still heavy in time and
#: memory; such a still goes to the host an encode would use (the most memory).
HEAVY = re.compile(r'renderer="pathtrace"|<medium\b')


def frame_host(text: str) -> str:
    """Where a still of this document renders: the still host, or for a heavy
    document the encode host."""
    from . import render
    return render.remote_host(None, "encode" if HEAVY.search(text) else "still")


class host_lock:
    """One kit render per host at a time, across every process on this machine:
    agents that start frames in the background queue here instead of piling
    path-traced renders onto one host."""

    def __init__(self, host: str) -> None:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", host or "local")
        self.path = Path(tempfile.gettempdir()) / f"yt-kit-render-{name}.lock"

    def __enter__(self) -> "host_lock":
        import fcntl
        self.fh = open(self.path, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        import fcntl
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def frame(doc: Path, at: float, *, host: str = "", out_dir: Path | None = None
          ) -> tuple[Path | None, list[str]]:
    """Render one frame of a document into frames/kit/ -- on the still host, or on
    the encode host when it is path traced or has a medium, one kit render per
    host at a time -- and run the eyeless frame checks: (png path or None, findings)."""
    from . import framecheck, render
    from .vfx import FILE_ATTRS, referenced_files
    doc = Path(doc).resolve()
    data = doc.read_bytes()
    text = data.decode("utf-8", "replace")
    host = host or frame_host(text)
    # The engine runs where every referenced file can be named below it: a
    # document under vfx/vNN/ that uses ../../props/... ships from the project.
    refs = [Path(os.path.normpath(doc.parent / m.group(1))) for m in
            re.finditer(r'\b(?:' + "|".join(FILE_ATTRS) + r')="([^"]+)"', text)
            if "://" not in m.group(1) and not m.group(1).startswith("#")]
    root = Path(os.path.commonpath([doc.parent] + [r for r in refs if r.exists()]))
    if root.is_file():
        root = root.parent
    doc_path = doc.relative_to(root).as_posix()
    files = referenced_files(text, doc.parent, root)
    files.pop(doc_path, None)
    with host_lock(host):
        started = time.monotonic()
        png, message = render.engine_render_still(data, at, host=host, files=files,
                                                  doc_path=doc_path)
        if png is None and "No such file or directory" in message:
            # The engine binary this process found on the host is gone (a
            # rebuild or a clean): find it again and try once more.
            getattr(render, "_HOSTS", {}).pop(host, None)
            png, message = render.engine_render_still(data, at, host=host, files=files,
                                                      doc_path=doc_path)
    if png is None and any(m in message for m in UNREACHABLE):
        # A host this process found usable stopped answering: probe it again,
        # and if it is down, render where the router now sends the job.
        getattr(render, "_HOSTS", {}).pop(host, None)
        fallback = frame_host(text)
        if fallback and fallback != host:
            with host_lock(fallback):
                started = time.monotonic()
                png, message = render.engine_render_still(data, at, host=fallback, files=files,
                                                          doc_path=doc_path)
            host = fallback
    seconds = time.monotonic() - started
    findings = [line for line in message.splitlines()[1:] if line.strip()]
    if png is not None:
        findings.append(f"note: {RENDER_NOTE} {seconds:.0f} s on {host or 'this machine'} "
                        f"({render_settings(text)})")
    if png is None:
        return None, [f"error: frame at {at:g}s of {doc.name} not rendered: "
                      f"{message.splitlines()[0] if message else 'no message'}"] + findings
    # An author's own previews go to frames/kit/, apart from the proof frames a
    # stage's gate renders and its reviewer reads.
    out = Path(out_dir or doc.parent / "frames" / "kit")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{doc.stem.replace('.scene', '')}__{at:g}s.png"
    path.write_bytes(png)
    findings += framecheck.judge_frame(png, at, doc.stem)
    return path, findings


# --------------------------------------------------------------------------- #
# the workspace commands
# --------------------------------------------------------------------------- #

def _describe(element: str) -> None:
    d = sdk().describe(element).to_dict()
    print(f"<{element}>  {' '.join(str(d.get('doc', '')).split())[:400]}")
    print("  min version:", d.get("min_version"))
    for a in d.get("attributes", []):
        t = a.get("type") or {}
        kind = t.get("enum") or t.get("name") or t.get("base") or "?"
        if isinstance(kind, list):
            kind = "|".join(kind)
        doc = " ".join(str(a.get("doc", "")).split())[:200]
        print(f"  @{a['xml_name']}: {kind}" + (f"  -- {doc}" if doc else ""))
    kids = [c.get("xml_name") or c.get("class_name")
            for c in (d.get("children") or []) if isinstance(c, dict)]
    if kids:
        print("  children:", ", ".join(str(k) for k in kids))


def _print_checked(checked: dict) -> None:
    for f in checked["sdk"]:
        print(f"  {f}")
    if checked["engine"] is None:
        print("  note: no engine reachable; only the SDK's checks ran")
    else:
        for f in checked["engine"]:
            print(f"  {f}")
    print(f"  {len(checked['errors'])} error(s)")


def main(argv: list[str]) -> int:
    from . import env
    env.load(str(AUTOMATION_ROOT / ".env"))      # the render hosts live there
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "elements":
        print("\n".join(sorted(sdk().element_names())))
        return 0
    if cmd == "describe" and rest:
        _describe(rest[0])
        return 0
    if cmd == "enum" and len(rest) > 1:
        from .scene_sdk import enum_of
        print(json.dumps(enum_of(rest[0], rest[1]) or "(free)"))
        return 0
    if cmd == "build" and rest:
        result = build(Path(rest[0]))
        if result.get("error"):
            print(f"error: {result['error']}")
            return 1
        for checked in result["documents"]:
            print(f"{checked['path']}  (deterministic, provenance written)")
            _print_checked(checked)
        return 0 if result["ok"] else 1
    if cmd == "check" and rest:
        checked = check_document(Path(rest[0]))
        print(checked["path"])
        _print_checked(checked)
        return 1 if checked["errors"] else 0
    if cmd == "frame" and rest:
        at = float(rest[rest.index("--at") + 1]) if "--at" in rest else 0.0
        path, findings = frame(Path(rest[0]), at)
        print(f"frame: {path if path else '(not rendered)'}")
        for f in findings:
            print(f"  {f}")
        if path:
            print("\nnow Read the PNG and judge it: validation cannot see a picture.")
        return 1 if path is None or any(f.startswith("error") for f in findings) else 0
    print(KIT_CLI.split('"""')[1])
    return 2
