"""Where one video's files live, and how each stage finds the stage before it.

Every video gets a directory under the work root, named after its title:

    work/how-euv-light-is-made/
        idea.json          the idea itself, written by whichever stage ran first
        dossier.json       the research, plus README.md, claims.csv, raw/, ...
        styles.json .md    the visual direction
        script.json .md    the screenplay, plus narration.txt, shotlist.csv
        voice/ voice.json  the narration, with narration.vtt and .srt
        vfx.json .md       the animation plan, plus vfx.csv
        sound.json .md     the sound design, plus cues.csv
        scene.xml          the compiled scene-render document
        video.mp4          what the engine rendered, plus render.json

The research dossier writes straight into that directory, so every later stage
can find what it needs without being handed a path: `yt script --title euv`
picks up the dossier and the visual direction by itself. An explicit flag
always wins over what was found.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .core import Idea, _key, compact, slugify, write_json

DEFAULT_WORK = "work"

#: The file each artifact lives in, inside a project directory.
ARTIFACTS = {
    "idea": "idea.json",
    "dossier": "dossier.json",
    "styles": "styles.json",
    "styles_md": "styles.md",
    "script": "script.json",
    "script_md": "script.md",
    "narration": "narration.txt",
    "shotlist": "shotlist.csv",
    "voice": "voice.json",
    "retime": "retime.json",
    "captions": "narration.vtt",
    "vfx": "vfx.json",
    "vfx_md": "vfx.md",
    "props": "props.json",
    "keyframe": "keyframe.json",
    "sound": "sound.json",
    "scene": "scene.xml",
    "build": "build.json",
    "video": "video.mp4",
    "render": "render.json",
    "sound_md": "sound.md",
    "cues": "cues.csv",
    "pilot": "pilot.json",
    "gate": "gate.json",
    "review": "review.json",
    "qa": "qa.json",
    "retention": "retention.json",
}

#: Stage name -> the artifact that proves it ran, in pipeline order.
STAGES = (("research", "dossier"), ("styles", "styles"),
          ("script", "script"), ("props", "props"), ("voice", "voice"),
          ("retime", "retime"), ("keyframe", "keyframe"), ("vfx", "vfx"),
          ("sound", "sound"), ("build", "scene"), ("render", "video"))

IDEAS_FILE = "ideas.json"
STORE_FILE = "ideas.store.json"


@dataclass
class Project:
    """One video's directory.

    `standalone` marks a run anchored to an explicit input file rather than a
    project: the stage reads and writes beside that file and plants no
    idea.json there.
    """

    root: Path
    standalone: bool = False

    # -- paths -------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.root / ARTIFACTS[name]

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def out(self, name: str, override: str | None = None) -> str:
        """Where to write: what was asked for, else the conventional place."""
        return override or str(self.path(name))

    def source(self, name: str, override: str | None = None) -> str | None:
        """An earlier stage's file: what was asked for, else what is there."""
        if override:
            return override
        return str(self.path(name)) if self.has(name) else None

    def create(self) -> Project:
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    # -- the idea ----------------------------------------------------------

    def save_idea(self, idea: Idea) -> None:
        row = compact(idea)
        path = self.path("idea")
        if path.exists():
            try:
                if json.loads(path.read_text(encoding="utf-8")) == row:
                    return
            except (OSError, json.JSONDecodeError):
                pass
        write_json(path, row)

    def load_idea(self) -> Idea | None:
        """The project's idea, from idea.json or failing that the dossier.

        A dossier records the idea it was built from, which is what makes a
        research directory produced elsewhere usable as a project.
        """
        for name, key in (("idea", None), ("dossier", "idea")):
            path = self.path(name)
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if key:
                raw = raw.get(key) if isinstance(raw, dict) else None
            if isinstance(raw, dict) and raw.get("title"):
                return Idea.from_raw(raw)
        return None

    @property
    def title(self) -> str:
        idea = self.load_idea()
        return idea.title if idea else self.root.name

    def done(self) -> list[str]:
        return [stage for stage, artifact in STAGES if self.has(artifact)]


# --------------------------------------------------------------------------- #
# choosing an idea
# --------------------------------------------------------------------------- #

def _rows(source: str) -> list[dict]:
    """The ideas in a file: a bare list, or {"ideas": [...]}."""
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read {source}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{source} is not valid JSON: {exc}") from exc
    rows = data.get("ideas", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise SystemExit(f"{source} does not hold a list of ideas")
    return [r for r in rows if isinstance(r, dict)]


def pick_idea(args: argparse.Namespace, *, loose: bool = False) -> Idea:
    """Resolve an Idea from `-`, --from with --index/--title, or bare fields.

    With `loose`, a --title that matches nothing in the ideas file is taken
    as a title of its own rather than an error. That is how the default
    ideas.json is treated: the operator never named it, so failing to match
    it would be failing on a file they did not ask about.
    """
    index = getattr(args, "index", None)
    title = getattr(args, "title", None)
    source = getattr(args, "source", None)

    if getattr(args, "stdin", None):
        raw = json.loads(sys.stdin.read())
        if isinstance(raw, list):
            n = (index or 1) - 1
            if not 0 <= n < len(raw):
                raise SystemExit(f"--index {n + 1} out of range (1-{len(raw)}) "
                                 "for the list on stdin")
            raw = raw[n]
        return Idea.from_raw(raw)

    if source:
        rows = _rows(source)
        if title:
            wanted = _key(title)
            matches = [r for r in rows if wanted in _key(str(r.get("title", "")))]
            if len(matches) == 1:
                return Idea.from_raw(matches[0])
            if len(matches) > 1:
                listing = "\n".join(f"  - {m['title']}" for m in matches[:10])
                raise SystemExit(f"{title!r} matches {len(matches)} ideas in "
                                 f"{source}:\n{listing}")
            if not loose:
                raise SystemExit(f"no idea in {source} matches {title!r}")
            # Nothing matched and nobody named the file: it is a new title.
        else:
            n = index or 1
            if not 1 <= n <= len(rows):
                raise SystemExit(f"--index {n} out of range (1-{len(rows)}) "
                                 f"in {source}")
            return Idea.from_raw(rows[n - 1])

    if title:
        return Idea(title=title,
                    genre=getattr(args, "genre", None) or "explainer",
                    description=getattr(args, "description", None) or title)

    raise SystemExit("give --from FILE (with --index or --title), --title, "
                     "or - to read one idea on stdin")


# --------------------------------------------------------------------------- #
# finding a project
# --------------------------------------------------------------------------- #

def work_dir(args) -> Path:
    path = Path(getattr(args, "work", None) or DEFAULT_WORK)
    path.mkdir(parents=True, exist_ok=True)
    return path


def projects(work: Path) -> list[Project]:
    """Every project directory, most recently touched first."""
    if not work.exists():
        return []
    found = [Project(p) for p in work.iterdir()
             if p.is_dir() and not p.name.startswith(".")]
    return sorted(found, key=lambda p: p.root.stat().st_mtime, reverse=True)


def matches(work: Path, needle: str) -> list[Project]:
    """Every project whose directory name or stored title contains `needle`."""
    wanted = _key(needle)
    if not wanted:
        return []
    return [p for p in projects(work)
            if wanted in _key(p.root.name) or wanted in _key(p.title)]


def match(work: Path, needle: str) -> Project | None:
    """The one project `needle` names, or None when it names none or several."""
    hits = matches(work, needle)
    return hits[0] if len(hits) == 1 else None


def _listing(found: list[Project]) -> str:
    return "\n".join(f"  {p.root.name}  [{', '.join(p.done()) or 'empty'}]"
                     for p in found[:15])


def add_inputs(parser, *names: str) -> None:
    """Declare `--<name> FILE` for each upstream artifact a stage reads, so it
    can be handed files instead of a project. The dest is `in_<name>`, so a
    stage's own flags (`--voice` as a voice id, `--frames` as a count) are
    never shadowed."""
    for name in names:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=f"in_{name}",
                            metavar="FILE",
                            help=f"the {ARTIFACTS.get(name, name)} to read "
                                 "(default: the project's)")


def override(args, name: str) -> str | None:
    """The file given for an artifact, if any."""
    return getattr(args, f"in_{name}", None) or None


def _idea_named(title: str, args) -> Idea:
    return Idea(title=title,
                genre=str(getattr(args, "genre", None) or ""),
                description=str(getattr(args, "description", None) or ""))


def resolve(args, *, idea: Idea | None = None,
            inputs: tuple[str, ...] = ()) -> tuple[Idea, Project]:
    """Work out which video this invocation is about, and where it lives.

    In order: an explicit --project (authoritative, even before it has an
    idea: one is made from --title or the directory's name), an explicit
    input file among `inputs` (a standalone run, anchored to that file's
    directory, planting nothing), a --title that names a project already on
    disk, the idea selectors (--from/--index/--title/-, defaulting to the
    ideas.json in the work root), a caller-supplied fallback, and finally the
    only project there is.
    """
    work = work_dir(args)
    chosen: Project | None = None
    if getattr(args, "project", None):
        chosen = Project(Path(args.project))
    found = chosen.load_idea() if chosen else None
    if chosen is not None and found is None:
        title = (getattr(args, "title", None)
                 or chosen.root.name.replace("-", " ").replace("_", " "))
        found = _idea_named(str(title), args)
        if getattr(args, "title", None):
            args.title = None            # consumed; not an ideas.json query

    if chosen is None:
        anchor = next((override(args, name) for name in inputs
                       if override(args, name)), None)
        if anchor is None:
            anchor = next((getattr(args, name, None) for name in inputs
                           if isinstance(getattr(args, name, None), str)
                           and getattr(args, name, None)), None)
        if anchor:
            path = Path(anchor)
            stem = path.name.split(".")[0] or path.parent.name
            title = getattr(args, "title", None) or stem.replace("-", " ").replace("_", " ")
            return (_idea_named(str(title), args),
                    Project(path.parent if path.parent != Path("") else Path("."),
                            standalone=True))

    selectors = [a for a in ("source", "title", "index", "stdin")
                 if getattr(args, a, None)]
    if found is None and selectors:
        named_file = bool(getattr(args, "source", None))
        if getattr(args, "title", None) and not named_file:
            hits = matches(work, args.title)
            if len(hits) > 1:
                # Falling through would take the substring for a title of its
                # own and start an empty project beside the ones it matched.
                raise SystemExit(
                    f"--title {args.title!r} matches {len(hits)} videos. Be "
                    f"more specific, or pass --project:\n{_listing(hits)}")
            if hits:
                chosen, found = chosen or hits[0], hits[0].load_idea()
        if found is None:
            if not named_file:
                default = work / IDEAS_FILE
                if default.exists():
                    args.source = str(default)
            found = pick_idea(args, loose=not named_file)

    if found is None and idea is not None:
        found = idea
    if found is None:
        here = projects(work)
        if len(here) == 1:
            chosen, found = here[0], here[0].load_idea()
        elif here:
            raise SystemExit("which video? pass --title, --index or --project. "
                             f"in {work}:\n{_listing(here)}")
    if found is None:
        raise SystemExit("no idea to work from: pass --title, --from FILE, "
                         "--project DIR, or - to read one on stdin")

    project = (chosen or Project(work / slugify(found.title))).create()
    project.save_idea(found)
    return found, project
