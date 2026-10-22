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

from claude_engine import ClaudeError, _partial_cost

from . import framecheck
from .scene_sdk import sdk

KIT_DIR = "kit"

#: Reference libraries of complete SDK programs that rendered on this engine,
#: one folder each with an INDEX.md (see kit/REFERENCES.md in a workspace). They
#: are read, never run, and live outside git; SR_KIT_REFERENCES moves them.
REFERENCES_DIR = Path(os.environ.get("SR_KIT_REFERENCES")
                      or Path(__file__).resolve().parent.parent / "_tmp" / "kit-references")


def reference_libraries() -> list[Path]:
    """The reference libraries present: folders under REFERENCES_DIR with an INDEX.md."""
    if not REFERENCES_DIR.is_dir():
        return []
    return sorted(p for p in REFERENCES_DIR.iterdir() if (p / "INDEX.md").is_file())


def references_text(libraries: list[Path]) -> str:
    """kit/REFERENCES.md: where each library is and how an author uses it."""
    lines = ["# Reference libraries", "",
             "Complete programs written with the SDK, built and rendered on this engine, far",
             "more of them than kit/examples/. Start from a library's INDEX.md: it lists every",
             "program by subject and by the SDK elements and effect types it uses, with a",
             "thumbnail of what it renders. Read a program to see how a construct is written",
             "and copy the construction into your own program. Never run them (they import",
             "helpers of their own library), and their colours, sizes and texts are not a look",
             "for this film.", ""]
    for lib in libraries:
        lines.append(f"- `{lib}/INDEX.md`")
    return "\n".join(lines) + "\n"
EXAMPLES_SRC = Path(__file__).resolve().parent / "kit" / "examples"
#: What authors went to the engine's source for, stamped with the engine version
#: it was verified on; ENGINE.md prints it and flags it when the version differs.
FACTS_FILE = Path(__file__).resolve().parent / "kit" / "engine_facts.json"
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

`save` writes nothing while a module is being imported. A module the program
imports, `look.py` among them, may end with its own build and save: importing
it writes nothing, and a helper of its that saves writes when the program
(`__main__`) calls it. The build records the hash of every module loaded from
this directory beside the document, in its provenance.
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


def _imports():
    """Every module loaded from this directory, by file name, with its hash:
    what the document was built with besides the program."""
    out = {{}}
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if name == "__main__" or not file:
            continue
        file = os.path.abspath(file)
        if os.path.dirname(file) != _here:
            continue
        try:
            with open(file, "rb") as fh:
                out[os.path.basename(file)] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            pass
    return dict(sorted(out.items()))


def _importing():
    """Whether a module is being imported somewhere up the stack: its top-level
    code is a `<module>` frame that is not `__main__`. A helper the look defines
    and the program calls has no such frame, so its save writes."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name == "<module>" and frame.f_globals.get("__name__") != "__main__":
            return True
        frame = frame.f_back
    return False


def save(scene, path):
    """Check the document, write it as pretty XML, record it for the build.
    Called while a module is being imported, write nothing."""
    path = os.path.abspath(path)
    if _importing():
        return path
    findings = [str(f) for f in S.check(scene)]
    data = scene.to_bytes(layout="pretty")
    with open(path, "wb") as fh:
        fh.write(data)
    record = os.environ.get("SRKIT_RECORD")
    if record:
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                                 "findings": findings, "imports": _imports()}}) + "\\n")
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
    python3 kit.py precheck <file.scene.xml|frame.png>
                                            the reviewer's mechanical checks (framing,
                                            contrast steps, haze, overlapping labels) on
                                            a document and the frames already rendered
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


def engine_facts() -> dict:
    """The facts file: {"verified_on": version, "facts": [{topic, text, source}]}."""
    try:
        data = json.loads(FACTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"verified_on": "", "facts": []}
    return data if isinstance(data, dict) else {"verified_on": "", "facts": []}


def facts_lines(version: str, facts: dict) -> list[str]:
    """ENGINE.md's facts section: each fact, stamped, flagged when the engine moved on."""
    rows = [f for f in facts.get("facts") or [] if isinstance(f, dict) and f.get("text")]
    if not rows:
        return []
    verified = str(facts.get("verified_on") or "?")
    lines = ["", "## Facts measured on the engine", "",
             f"Facts verified on {verified}"
             + ("." if version == verified else
                f"; this engine is {version}: re-check each one with a frame before relying "
                "on it."), ""]
    for f in rows:
        lines.append(f"- **{f.get('topic', '')}**: {f['text']} (source: {f.get('source', '?')})")
    return lines


def engine_text(engine: dict, notes: list[dict], measured: list[dict] | None = None,
                facts: dict | None = None) -> str:
    """ENGINE.md: the manifest, the facts, the renderer notes and anything measured."""
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
    eng_version = str(eng.get("version", "?"))
    lines += facts_lines(eng_version, engine_facts() if facts is None else facts)
    lines += ["", "## What a frame costs", "",
              "Every frame `kit.py frame` renders reports how long it took and with which "
              "settings (`note: rendered in ...`). A path-traced frame's time grows with "
              "`pathSamples` (roughly in proportion) and with lights and bounces, a clay "
              "object's with its blob count and `resolution`; a raster "
              f"frame takes seconds. `kit.py frame` stops a still at {STILL_BUDGET} s and "
              "names the cost driver; a still that runs past "
              f"{STILL_DEADLINE} s is abandoned "
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


#: The approved look's program, importable from every section's workspace.
LOOK_MODULE = "look.py"


def install(directory: Path, *, frame: tuple[int, int] | None = None, fps: int = 30,
            host: str = "", measured: list[dict] | None = None,
            look: Path | None = None) -> dict:
    """Write the kit into `directory`. `frame` writes frame.json (a stage that
    already writes one passes None). `look` is the approved look's program,
    copied byte for byte as `look.py` beside srkit.py so a section program can
    import it. Returns what was installed."""
    directory = Path(directory)
    kit = directory / KIT_DIR
    (kit / "examples").mkdir(parents=True, exist_ok=True)
    (directory / "srkit.py").write_text(SRKIT.format(sr_root=str(sr_root())), encoding="utf-8")
    (directory / "kit.py").write_text(KIT_CLI.format(automation_root=str(AUTOMATION_ROOT)),
                                      encoding="utf-8")
    installed_look = None
    if look is not None and Path(look).is_file():
        target = directory / LOOK_MODULE
        if Path(look).resolve() != target.resolve():
            target.write_bytes(Path(look).read_bytes())
        installed_look = LOOK_MODULE
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
    libraries = reference_libraries()
    if libraries:
        (kit / "REFERENCES.md").write_text(references_text(libraries), encoding="utf-8")
    return {"dir": str(directory), "engine_source": engine.get("source"),
            "renderer_notes": len(notes), "examples": [p.name for p in example_files()],
            "look": installed_look}


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
    "you never invent an asset file, a font or a value the schema does not accept. "
    "You write and edit files with the Write and Edit tools; the shell is for running "
    "`python3 kit.py ...` and your program, and a shell command that writes a file "
    "(a heredoc, `>`, `sed -i`) is denied. A denial names that one command, never the "
    "shell: when one is denied, do it with the right tool and keep building."
)

#: The tools such an agent gets: its workspace's files and a shell for the kit.
AUTHOR_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")

#: Project directories an author may read besides its workspace: the fetched props,
#: the approved look and the research assets. Read-only.
AUTHOR_READ_DIRS = ("props", "keyframe", "downloads")

#: The shell commands an author may run: the kit, the stage's frame command and its
#: own program. Everything else is denied (`dontAsk`), so the shell is no way out of
#: the workspace.
AUTHOR_SHELL = ("python3 kit.py *", "python3 sr.py *")


def author_config(workspace: Path, *, project_root: Path | None = None,
                  programs: tuple[str, ...] | list[str] = ()) -> dict:
    """EngineConfig fields that confine an authoring session: Read, Glob and Grep to
    the workspace plus the project's read-only directories (`restricted`, `add_dirs`),
    Write and Edit denied under those directories, and Bash an allow-list. The bare
    tools are not auto-allowed: `auto_allow_tools` would allow every shell command."""
    dirs: list[str] = []
    if project_root:
        dirs = [str(Path(project_root) / name) for name in AUTHOR_READ_DIRS
                if (Path(project_root) / name).is_dir()]
    dirs += [str(lib) for lib in reference_libraries()]     # read, never written
    allowed = [t for t in AUTHOR_TOOLS if t != "Bash"]
    allowed += [f"Bash({cmd})" for cmd in AUTHOR_SHELL]
    allowed += [f"Bash(python3 {p})" for p in programs]
    return {"tools": AUTHOR_TOOLS, "allowed_tools": tuple(allowed), "auto_allow_tools": False,
            # "//" marks an absolute path; a single "/" reads as relative to the project root
            "disallowed_tools": tuple(f"{t}(/{d}/**)" for d in dirs for t in ("Write", "Edit")),
            "permission_mode": "dontAsk", "restricted": True, "add_dirs": tuple(dirs),
            "cwd": workspace}

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
- `kit/REFERENCES.md`, when present: libraries of hundreds of complete programs that
  rendered, indexed by subject and technique (`grep` their INDEX.md for an element or
  an effect type, Read a thumbnail); read and adapt them, never run them.
- `python3 kit.py describe <element>` before writing an attribute you have not used.
- `python3 kit.py build <program.py>` until it reports 0 errors and a deterministic
  document; then `python3 kit.py frame <doc> --at <seconds>` and Read every PNG.
- `python3 kit.py precheck <doc>` runs the mechanical part of the checklist below on
  the document and the frames you rendered; run it before you call a frame done.
- Stay in your workspace: the kit, the examples, this brief and the project directories
  it names are all you need; anything else is denied.
- Write the program with the Write tool and change it with Edit. The shell runs
  `python3 kit.py ...`, `python3 sr.py ...` and your program; a shell command that
  writes a file (a heredoc, `>`, `sed -i`) is denied. A denial is about that one
  command, not the shell: the build and the frames still run.
- `kit.py` prints the time left on every build and frame (`SRKIT_DEADLINE`). Keep a
  program that builds and a still you have looked at: when your time reaches 80% you
  are stopped and asked to build and render the current program at once.

## What the reviewer checks

The reviewer judges every frame against this checklist, so do not spend a round to
learn it. `[pre-check]` items you can test yourself; `[review]` items need your eyes.

""" + framecheck.checklist_text() + """
"""

#: What a brief says about the approved look's module, so every stage that
#: installs one says the same: import it, redefine nothing it provides.
LOOK_BRIEF = (
    f"`{LOOK_MODULE}` in your workspace is that program, byte for byte. Import what it "
    "provides -- `from look import ...` its palette and type constants, the helper "
    "functions that build its labels, formulas, cards, materials, lights and recurring "
    "objects, and its camera language -- and redefine nothing it provides; change only "
    "what your shots require. Importing it writes nothing (`save` writes nothing during an "
    "import), and a helper of its that saves writes when your program calls it. Never edit "
    f"`{LOOK_MODULE}`: the build records its hash in your "
    "document's provenance, and a changed look lapses it.")


def time_line(timeout: float) -> str:
    """The brief's statement of the author's clock: the session timeout is the 80%
    mark of a round, the rest of the budget is kept for the finish."""
    budget = round_budget(timeout)
    return (f"## Your clock\n\nA round is {budget / 60:g} minutes. At {timeout / 60:g} minutes "
            "(80%) you are stopped and asked to build the current program and render a still "
            "of it, which is then reviewed; what is not built by then is lost. Plan for a "
            "program that builds early, and improve it in steps.\n")


# --------------------------------------------------------------------------- #
# the author's clock
# --------------------------------------------------------------------------- #

#: The share of a round's budget at which an author is stopped and asked to build
#: and render what it has. An author session's timeout is that mark.
CUTOFF = 0.8

#: The environment variable by which the kit learns when the round ends (epoch seconds).
DEADLINE_VAR = "SRKIT_DEADLINE"

#: Less than this left of the budget, a finish call could not build and render anything.
MIN_FINISH = 60.0


def now() -> float:
    """The clock the rounds are kept by (a seam: tests replace it)."""
    return time.time()


def round_budget(timeout: float) -> float:
    """The whole budget of a round whose session timeout is `timeout` (the 80% mark)."""
    return timeout / CUTOFF


def time_left_line(env: dict | None = None) -> str:
    """What `kit.py` prints on every build and frame: the time left in the round,
    or nothing when no deadline is set (a person running the kit by hand)."""
    raw = (os.environ if env is None else env).get(DEADLINE_VAR)
    try:
        left = float(raw) - now()
    except (TypeError, ValueError):
        return ""
    if left <= 0:
        return ("time left: none, past the deadline: build and render what you have now")
    minutes, seconds = divmod(int(left), 60)
    return (f"time left: {minutes} min {seconds} s of this round; keep a program that "
            "builds and a still you have looked at")


def finish_prompt(program: str = "the program", document: str = "the document",
                  frame_cmd: str | None = None) -> str:
    """What an author is asked at 80% of its round: stop and show what it has."""
    return (f"Your time is nearly up. Stop changing the look now. Build the current program "
            f"(`python3 kit.py build {program}`), render a still of it "
            f"(`{frame_cmd or f'python3 kit.py frame {document} --at 1'}`), Read the PNG, and "
            "reply with one line. Do it now; start nothing new.")


#: The share of a session's dollar cap at which an author is stopped and asked to build
#: and render what it has, as the clock does at CUTOFF.
BUDGET_CUTOFF = 0.8

#: The result subtype the claude CLI ends a session with at its `--max-budget-usd`.
BUDGET_STOP = "error_max_budget_usd"

#: The environment variable by which the kit learns what is left of the session's cap: the
#: path of a small JSON file `stream_round` keeps current.
BUDGET_FILE_VAR = "SRKIT_BUDGET_FILE"
BUDGET_FILE = ".srkit-budget"

#: Less than this left of the cap, a finish call could not build and render anything.
MIN_FINISH_USD = 0.25

_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")


def budget_line(cap: float) -> str:
    """What the author is told of its session's money, as it is told its time."""
    return (f"## Your budget\n\nThis session may spend ${cap:g}. At {BUDGET_CUTOFF:.0%} "
            f"(${cap * BUDGET_CUTOFF:.2f}) you are stopped and asked to build the current "
            "program and render a still of it, which is then reviewed; what is not built by "
            "then is lost. `kit.py` prints the budget left on every build and frame.\n")


def budget_left_line(env: dict | None = None) -> str:
    """What `kit.py` prints beside the time left: the session's money left, or nothing
    when no budget file is set (a person running the kit by hand)."""
    path = (os.environ if env is None else env).get(BUDGET_FILE_VAR)
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        left, cap = float(state["left"]), float(state["cap"])
    except (TypeError, ValueError, KeyError, OSError):
        return ""
    if left <= 0:
        return "budget left: none: build and render what you have now"
    return (f"budget left: ${left:.2f} of ${cap:g} for this session; keep a program that "
            "builds and a still you have looked at")


class _Meter:
    """The dollars a session has spent so far, from the stream's per-message usage
    (the latest per message id), priced as the engine prices a cut-off call. A call
    whose model has no price is not metered here: `stream_round` runs it under the
    mark as the CLI's own budget instead."""

    def __init__(self, cap: float, path: Path | None):
        self.cap, self.path, self.done = cap, path, 0.0
        self.seen: dict[str, tuple[str | None, dict]] = {}
        self.write()

    def note(self, event) -> None:
        raw = getattr(event, "raw", None) or {}
        if raw.get("type") == "result":         # the call ended: its cost is known
            self.done += float(raw.get("total_cost_usd") or self.call_spent() or 0.0)
            self.seen.clear()
        elif raw.get("type") == "assistant":
            inner = raw.get("message") or {}
            if isinstance(inner.get("usage"), dict):
                self.seen[str(inner.get("id") or len(self.seen))] = (
                    inner.get("model"), inner["usage"])
        self.write()

    def tokens(self) -> dict[str, int]:
        total = dict.fromkeys(_TOKEN_KEYS, 0)
        for _, usage in self.seen.values():
            for key in _TOKEN_KEYS:
                total[key] += int(usage.get(key) or 0)
        return total

    def call_spent(self) -> float | None:
        cost = 0.0
        for model, usage in self.seen.values():
            part = _partial_cost({k: int(usage.get(k) or 0) for k in _TOKEN_KEYS}, model)
            if part is None:
                return None
            cost += part
        return cost

    def spent(self) -> float:
        return self.done + (self.call_spent() or 0.0)

    def left(self) -> float:
        return max(0.0, self.cap - self.spent())

    def at_mark(self) -> bool:
        return (bool(self.seen) and self.call_spent() is not None
                and self.spent() >= BUDGET_CUTOFF * self.cap)

    def book(self, engine) -> None:
        """The stream was abandoned at the mark, so the engine saw no end to book:
        book what it spent as a cut-off call."""
        cost = self.call_spent()
        if not self.seen or cost is None:
            return
        lock = getattr(engine, "_lock", None)
        if lock is not None:
            with lock:
                engine.usage.add_partial(self.tokens(), cost=cost, estimated=True)
        else:
            engine.usage.add_partial(self.tokens(), cost=cost, estimated=True)
        self.done += cost
        self.seen.clear()
        self.write()

    def write(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(json.dumps({"left": round(self.left(), 4), "cap": self.cap}),
                                 encoding="utf-8")
        except OSError:
            pass


def stream_round(engine, prompt: str, *, system: str, resume=False, finish: str | None = None,
                 budget_usd: float | None = None):
    """One author round: yield the engine's events, as `engine.stream` does, with the
    clock kept. The session's timeout is the 80% mark of the round's budget; the
    deadline reaches the kit through `SRKIT_DEADLINE`. A session cut off at the mark
    is resumed once with `finish` (build and render the current program) and the time
    left of the budget; a failure before the mark, any other failure, no session to
    resume or no time left is raised as it was.

    With `budget_usd`, the session's dollar cap is the call's hard cap and the spend is
    metered; at BUDGET_CUTOFF of the cap the stream is stopped and the session resumed
    once with `finish` and the rest of the cap, the same way. What is left is written
    where the kit reads it (`SRKIT_BUDGET_FILE`)."""
    config = getattr(engine, "config", None)
    if config is None:              # an engine that carries no clock is simply streamed
        yield from engine.stream(prompt, system=system, **({"resume": resume} if resume else {}))
        return
    timeout = float(config.timeout or 0)
    started = now()
    deadline = started + round_budget(timeout) if timeout else None
    if deadline is not None:
        config.env = dict(config.env or {}, **{DEADLINE_VAR: f"{deadline:.0f}"})
    meter = None
    if budget_usd:
        config.max_turns_budget_usd = budget_usd
        cwd = getattr(config, "cwd", None)
        meter = _Meter(float(budget_usd), Path(cwd) / BUDGET_FILE if cwd else None)
        if meter.path is not None:
            config.env = dict(config.env or {}, **{BUDGET_FILE_VAR: str(meter.path)})
    # A model with no configured price cannot be metered on the stream. The CLI
    # enforces the budget itself, so its session runs under the mark and the CLI's
    # own budget stop (with the exact cost it reports) is the mark.
    staged = meter is not None and _partial_cost(
        {"output_tokens": 1}, getattr(config, "model", None)) is None
    if staged:
        config.max_turns_budget_usd = BUDGET_CUTOFF * float(budget_usd)
    marked = stopped = False
    left = None
    try:
        stream = engine.stream(prompt, system=system, **({"resume": resume} if resume else {}))
        try:
            for event in stream:
                if meter is not None:
                    meter.note(event)
                yield event
                if staged and event.type == "result" and event.subtype == BUDGET_STOP:
                    stopped = True
                if meter is not None and meter.at_mark():
                    marked = True
                    break
        finally:
            if hasattr(stream, "close"):
                stream.close()
        if not (marked or stopped):
            return
    except ClaudeError as exc:
        reached = timeout and now() - started >= 0.95 * timeout
        session = getattr(engine, "session_id", None)
        left = deadline - now() if deadline is not None else 0.0
        if stopped:             # the CLI's exit 1 after its budget result
            if not (session and left >= MIN_FINISH and meter.left() >= MIN_FINISH_USD):
                raise
        else:
            if not (reached and "timed out" in str(exc).lower() and session
                    and left >= MIN_FINISH):
                raise
            if meter is not None and meter.left() < MIN_FINISH_USD:
                raise
    if marked or stopped:
        if marked:
            meter.book(engine)      # a CLI stop is no cut-off call: the engine booked it
        left = deadline - now() if deadline is not None else None
        if (not getattr(engine, "session_id", None) or meter.left() < MIN_FINISH_USD
                or (left is not None and left < MIN_FINISH)):
            if stopped:
                raise ClaudeError("the session stopped on its budget with too little left "
                                  "to finish")
            return
    if meter is not None:
        config.max_turns_budget_usd = meter.left()
    if left is not None:
        config.timeout = left
    try:
        for event in engine.stream(finish or finish_prompt(), system=system, resume=True):
            if meter is not None:
                meter.note(event)
            yield event
    finally:
        config.timeout = timeout
        if meter is not None:
            config.max_turns_budget_usd = budget_usd


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


#: The smallest text a phone viewer can read, as a share of the frame height
#: (54 px at 1080p): the house standard's floor for labels and numbers.
LEGIBLE_SHARE = 1 / 20

#: Elements whose `size` is the height of the text they draw.
TEXT_ELEMENTS = ("text", "textStyle", "formula")


def _number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _font_findings(nodes, local) -> list[str]:
    """One error per distinct font request that no shipped family answers: what
    `yt build` would refuse, said in the author's own round."""
    from . import fonts
    styles = {el.get("id"): dict(el.attrib) for el in nodes
              if local(el) == "textStyle" and el.get("id")}
    seen: set[tuple[str, str, str]] = set()
    out = []
    for el in nodes:
        req = fonts._request(local(el), dict(el.attrib), styles)
        if req is None or req in seen:
            continue
        seen.add(req)
        try:
            fonts.resolve(*req)
        except fonts.FontError as exc:
            who = " ".join(p for p in (local(el), el.get("id")) if p)
            out.append(f"error: rule:font: {who}: {exc}; write a family the repo ships, or a "
                       "list that ends in one (any other family fails `yt build`)")
    return out


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
    project = next((el for el in nodes if local(el) == "project"), None)
    try:
        height = float(project.get("height")) if project is not None else 0.0
    except (TypeError, ValueError):
        height = 0.0
    if height:
        floor = height * LEGIBLE_SHARE
        small = sorted({f"{el.get('id') or local(el)} ({float(el.get('size')):g} px)"
                        for el in nodes if local(el) in TEXT_ELEMENTS and _number(el.get("size"))
                        and float(el.get("size")) < floor})
        if small:
            out.append(f"warning: rule:legibility: text under {floor:.0f} px (1/20 of the frame "
                       f"height) cannot be read on a phone: {', '.join(small[:12])}"
                       + (" ..." if len(small) > 12 else "")
                       + "; enlarge every word that is part of the film (a style sheet's own "
                         "notes may stay small)")
    out += _font_findings(nodes, local)
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
        # What each document was built with besides the program: the workspace
        # modules loaded when it was saved (srkit, the look), by name and hash.
        imports = {r["path"]: r.get("imports") or {} for r in rows if "path" in r}
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
                      "imports": imports.get(path, {}),
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
    still the one it built (same bytes) and every workspace module the program
    imported is still the one it was built with (`imports`, beside the document)."""
    doc = Path(doc)
    try:
        record = json.loads(doc.with_suffix("").with_suffix(".provenance.json")
                            .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("document_sha256") != _sha(doc.read_bytes()):
        return None
    for name, digest in (record.get("imports") or {}).items():
        try:
            if _sha((doc.parent / name).read_bytes()) != digest:
                return None
        except OSError:
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

#: What `frame` allows a still, well under the ceiling above: an author's session
#: must not wait on a render that will not finish. A caller that needs the full
#: ceiling (the probe suite's verify frames) passes `budget=STILL_DEADLINE`.
STILL_BUDGET = 420


def clay_cost(text: str) -> tuple[int, int]:
    """(blob count, cells along the longest side of the largest clay object) of a
    document: the two things a clay surface's render time grows with."""
    blobs = len(re.findall(r"<blob\b", text))
    cells: list[int] = []
    for tag in re.findall(r"<object3D\b[^>]*>", text):
        if 'primitive="clay"' in tag:
            res = re.search(r'\bresolution="(\d+)"', tag)
            cells.append(int(res.group(1)) if res else 64)
    return blobs, max(cells, default=0)


def render_settings(text: str) -> str:
    """The renderer settings that decide a still's cost, as one phrase."""
    renderer = re.search(r'renderer="(\w+)"', text)
    samples = re.search(r'pathSamples="(\d+)"', text)
    bounces = re.search(r'maxBounces="(\d+)"', text)
    blobs, cells = clay_cost(text)
    clay = f", {blobs} blobs at {cells} cells" if blobs else ""
    if renderer and renderer.group(1) == "pathtrace":
        return (f"pathtrace, {samples.group(1) if samples else 64} samples, "
                f"{bounces.group(1) if bounces else 4} bounces"
                + (", a medium" if "<medium" in text else "") + clay)
    return "raster" + (", a medium (path traced at 4 samples)" if "<medium" in text else "") + clay


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


#: A wait for the host's render lock at least this long is reported apart.
WAIT_NOTE_SECONDS = 1.0


def frame(doc: Path, at: float, *, host: str = "", out_dir: Path | None = None,
          budget: float | None = None) -> tuple[Path | None, list[str]]:
    """Render one frame of a document into frames/kit/ -- on the still host, or on
    the encode host when it is path traced or has a medium, one kit render per
    host at a time -- and run the eyeless frame checks: (png path or None, findings).
    A still is stopped at `budget` seconds (STILL_BUDGET unless given) and the error
    names the document's cost drivers."""
    from . import render
    from .vfx import FILE_ATTRS, referenced_files
    budget = STILL_BUDGET if budget is None else budget

    def still(where: str) -> tuple[bytes | None, str]:
        try:
            return render.engine_render_still(data, at, host=where, files=files,
                                              doc_path=doc_path, timeout=budget,
                                              extra=refs, cacheable=refs is not None)
        except subprocess.TimeoutExpired:
            return None, f"error: the render ran past {budget:g} s"
    doc = Path(doc).resolve()
    data = doc.read_bytes()
    text = data.decode("utf-8", "replace")
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
    # Every file the document names, shipped or not, is part of the cache key; one
    # that cannot be read leaves the still out of the cache altogether.
    refs = render.reference_files(text, doc.parent)
    # A cache hit needs no host: it is served before any ssh health check and
    # without waiting for another render's lock.
    hit = None if refs is None else render.cached_still(
        data, at, host=host, files=files, doc_path=doc_path, extra=refs)
    waited = 0.0
    if hit:
        png, message = hit
        started = time.monotonic()
        host = host or message.rpartition("rendered earlier on ")[2]
    else:
        host = host or frame_host(text)
        asked = time.monotonic()        # the budget times the render, not this wait
        with host_lock(host):
            started = time.monotonic()
            waited = started - asked
            png, message = still(host)
            if png is None and "No such file or directory" in message:
                # The engine binary this process found on the host is gone (a
                # rebuild or a clean): find it again and try once more.
                getattr(render, "_HOSTS", {}).pop(host, None)
                png, message = still(host)
    if png is None and any(m in message for m in UNREACHABLE):
        # A host this process found usable stopped answering: probe it again,
        # and if it is down, render where the router now sends the job.
        getattr(render, "_HOSTS", {}).pop(host, None)
        fallback = frame_host(text)
        if fallback and fallback != host:
            with host_lock(fallback):
                started = time.monotonic()
                png, message = still(fallback)
            host = fallback
    seconds = time.monotonic() - started
    findings = [line for line in message.splitlines()[1:] if line.strip()]
    if png is not None and message.startswith("rendered from cache"):
        findings.append(f"note: served from cache (rendered earlier on "
                        f"{message.rpartition('rendered earlier on ')[2] or host or 'this machine'})")
    elif png is not None:
        findings.append(f"note: {RENDER_NOTE} {seconds:.0f} s on {host or 'this machine'} "
                        f"({render_settings(text)})")
    if waited >= WAIT_NOTE_SECONDS:
        findings.append(f"note: waited {waited:.0f} s for the host (its lock was held by "
                        "another render); that wait is not part of this still's cost")
    if png is None:
        # The host kills a render a few seconds before the budget; a run that long
        # with no picture was stopped for its cost, not refused by the engine.
        if message.startswith("error: the render ran past") or seconds >= budget - 10:
            return None, [f"error: frame at {at:g}s of {doc.name} not rendered: stopped at the "
                          f"{budget:g} s budget for a still (the ceiling is {STILL_DEADLINE} s). "
                          f"Cost drivers of this document: {render_settings(text)}. Lower them "
                          "(fewer blobs or cells, fewer pathSamples or maxBounces, no medium) "
                          "and judge composition at a setting that renders quickly"] + findings
        return None, [f"error: frame at {at:g}s of {doc.name} not rendered: "
                      f"{message.splitlines()[0] if message else 'no message'}"] + findings
    # An author's own previews go to frames/kit/, apart from the proof frames a
    # stage's gate renders and its reviewer reads.
    out = Path(out_dir or doc.parent / "frames" / "kit")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{doc.stem.replace('.scene', '')}__{at:g}s.png"
    path.write_bytes(png)
    judged = framecheck.judge_frame(png, at, doc.stem)
    findings += judged
    if not any(f.startswith("error") for f in judged):
        findings += framecheck.precheck(png, at, doc.stem, text)
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


def _print_time_left() -> None:
    line = time_left_line()
    if line:
        print(f"\n{line}")
    line = budget_left_line()
    if line:
        print(line if time_left_line() else f"\n{line}")


def _precheck(path: Path) -> int:
    """`kit.py precheck`: the checklist's mechanical items on a document (its labels
    and every frame already rendered into frames/kit/) or on one PNG."""
    found: list[str] = []
    if path.suffix.lower() == ".png":
        found = framecheck.precheck(path.read_bytes(), 0.0, path.stem)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        stem = path.name.replace(".scene.xml", "")
        found = framecheck.label_overlaps(text, stem)
        for png in sorted((path.parent / "frames" / "kit").glob(f"{stem}__*s.png")):
            try:
                at = float(png.stem.rsplit("__", 1)[1].rstrip("s"))
            except ValueError:
                at = 0.0
            found += framecheck.precheck(png.read_bytes(), at, stem)
    for f in found:
        print(f"  {f}")
    print(f"precheck: {len(found)} finding(s)" if found else "precheck: clean")
    _print_time_left()
    return 1 if any(f.startswith("error") for f in found) else 0


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
            _print_time_left()
            return 1
        for checked in result["documents"]:
            print(f"{checked['path']}  (deterministic, provenance written)")
            _print_checked(checked)
        _print_time_left()
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
        _print_time_left()
        return 1 if path is None or any(f.startswith("error") for f in findings) else 0
    if cmd == "precheck" and rest:
        return _precheck(Path(rest[0]))
    print(KIT_CLI.split('"""')[1])
    return 2
