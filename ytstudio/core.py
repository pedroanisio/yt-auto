"""The pieces every stage shares: an idea, the small helpers, usage accounting.

Lives on its own so that `workspace` can speak about ideas without importing a
stage, and so no two stages have to import each other to agree what an Idea
is, how a timecode reads, or what a project directory is called.
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from claude_engine import Usage


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

def fold(text: str) -> str:
    """Text with its accents dropped: "Explicação" -> "Explicacao".

    Latin-script titles have to survive becoming a directory name and a
    comparison key, so the accent is removed rather than the whole letter.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _key(name: str) -> str:
    """A loose comparison key: accents folded, everything else dropped."""
    return re.sub(r"[^a-z0-9]", "", fold(name).lower())


def slugify(text: str, limit: int = 60) -> str:
    """The directory name a project is addressed by, so it has to read well.

    Accents are folded rather than deleted ("Explicação" -> "explicacao", not
    "explica-o") and the cut lands on a word boundary, unless backing off
    would throw away more than half the name.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", fold(text).lower()).strip("-")
    if len(slug) > limit:
        cut = slug[:limit]
        if slug[limit] != "-":
            head = cut.rsplit("-", 1)[0]
            if len(head) >= limit // 2:
                cut = head
        slug = cut
    return slug.strip("-") or "idea"


def timecode(seconds: int | float) -> str:
    """Seconds as m:ss, the way a shot list reads."""
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def wrap(text: object, pad: str = "", width: int = 78) -> str:
    """Terminal paragraph: wrapped, with every line indented by `pad`."""
    return textwrap.fill(str(text), width=width, initial_indent=pad,
                         subsequent_indent=pad)


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #

def compact(obj: object) -> dict:
    """A dataclass or dict as a dict, with the fields never filled in left out."""
    row = asdict(obj) if not isinstance(obj, dict) else dict(obj)
    return {k: v for k, v in row.items() if v not in ("", [], None)}


def read_json(path: str | Path | None, *, warn: bool = True,
              shape: type | tuple[type, ...] = dict):
    """JSON from disk, or None when there is none to read.

    A directory means its dossier.json. No path at all is not a missing file,
    so it says nothing; a path that does not exist warns unless `warn` is off.
    `shape` is what the file must hold -- a dict by default, `list` for a
    manifest -- and anything else reads as None rather than as a surprise.
    """
    if not path:
        return None
    file = Path(path)
    if file.is_dir():
        file = file / "dossier.json"
    if not file.exists():
        if warn:
            print(f"warning: {file} not found, ignoring", file=sys.stderr)
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: cannot read {file} ({exc}), ignoring", file=sys.stderr)
        return None
    return data if isinstance(data, shape) else None


def write_json(path: str | Path, data: object) -> Path:
    """Pretty, UTF-8, trailing newline: the one way every stage writes JSON.

    Written beside the file and moved into place, so a write cut short (a
    crash, a Ctrl-C ending the interpreter under the sound thread) leaves the
    old record whole rather than a truncated one the next run counts as done.
    """
    target = Path(path)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


# --------------------------------------------------------------------------- #
# the idea
# --------------------------------------------------------------------------- #

#: The paper references that name no local file (AUTO-131): a DOI, an arXiv
#: id (new or old style, with a version or not) and an http(s) URL.
PAPER_DOI = re.compile(r"^(?:doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
PAPER_ARXIV = re.compile(
    r"^(?:arxiv:\s*)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?)$", re.I)
PAPER_HTTP = re.compile(r"^https?://", re.I)


def paper_is_local(ref: object) -> bool:
    """Whether a paper reference names a local file: anything set that is
    not a DOI, an arXiv id, an http(s) URL or already a `file:` name."""
    ref = str(ref or "").strip()
    return bool(ref) and not (ref.startswith("file:") or PAPER_HTTP.match(ref)
                              or PAPER_DOI.match(ref) or PAPER_ARXIV.match(ref))


@dataclass
class Idea:
    title: str
    genre: str
    description: str
    hook: str = ""
    length: str = ""
    keywords: list[str] = field(default_factory=list)
    #: What `yt ideas --rich` plans beyond the title (AUTO-100): the question
    #: the title raises and does not answer, what rides on it, what a typical
    #: viewer already believes about it, and the promise, title plus
    #: thumbnail concept in one sentence. Empty on an idea saved before them.
    question: str = ""
    stakes: str = ""
    viewer_belief: str = ""
    promise: str = ""
    score: float | None = None
    why: str = ""
    #: The rank decision's labels beside its score (`ideas.CURIOSITY`,
    #: `ideas.TITLE_CONCRETE`); the sort reads only the score.
    curiosity: str = ""
    title_concrete: str = ""
    #: The paper the film explains (`yt research --paper`, AUTO-131): an
    #: http(s) URL, a DOI, an arXiv id or a local PDF path. Empty for a film
    #: that is not about one paper, whose idea.json then never names it.
    paper: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> Idea:
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in known and v is not None}
        # `compact()` drops empty fields on the way out; an idea saved with
        # no genre or premise must still read back (only the title is required).
        if "title" in clean:
            clean.setdefault("genre", "")
            clean.setdefault("description", "")
        for key in ("title", "genre", "description", "hook", "length", "why",
                    "question", "stakes", "viewer_belief", "promise",
                    "curiosity", "title_concrete", "paper"):
            if key in clean:
                clean[key] = str(clean[key]).strip()
        return cls(**clean)

    def block(self) -> str:
        """The idea as every stage's prompt opens with it."""
        bits = [f"Title: {self.title}", f"Genre: {self.genre}",
                f"Premise: {self.description}"]
        if self.hook:
            bits.append(f"Planned hook: {self.hook}")
        if self.length:
            bits.append(f"Target length: {self.length}")
        if self.keywords:
            bits.append(f"Search intent: {', '.join(self.keywords)}")
        if self.paper:
            # A local file is named by its basename: every stage sends this
            # block to a model, and where the file lives is not the film's.
            name = (f"file:{os.path.basename(os.path.expanduser(self.paper.strip()))}"
                    if paper_is_local(self.paper) else self.paper)
            bits.append(f"Paper: {name}")
        return "\n".join(bits)


# --------------------------------------------------------------------------- #
# usage
# --------------------------------------------------------------------------- #

def _merge_usage(target: Usage, src: Usage) -> None:
    target.merge(src)
