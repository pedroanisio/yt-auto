"""The channel's standards, loaded into every stage that writes.

A rule like "the narration has to carry the message on first listen" binds the
screenplay, the visual direction, the animation plan and the sound at once.
Pasting it into each stage's SYSTEM prompt would put one rule in five places
that then drift, and passing it as --notes retypes it every run. So it lives in
a file:

    work/house.md               the channel's standard
    work/<slug>/house.md        this video's own, overriding sections by name
    --house FILE                somewhere else entirely
    --no-house                  ignore it for this run

Sections are Markdown headings, matched by stage name, and each stage is given
`## all` plus its own:

    ## all          every stage that writes
    ## script       the screenplay
    ## styles       the visual direction
    ## sound        effects, score and mix
    ## vfx          the animation plan

Anything before the first heading counts as `## all`. A project file's section
replaces the channel's section of the same name; sections it does not mention
still apply.

The order a stage sees is deliberate: its SYSTEM prompt says what it is, these
standards say how this channel works, the dossier and the direction say what is
true of this video, and --notes outranks all of it for one run. The digest of
what was loaded goes into the stage's manifest, so an artifact says which
standards produced it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "house.md"
HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
ALL = "all"

#: The stages that read standards. The rest deal in facts, not craft.
STAGES = ("script", "styles", "sound", "vfx")


def parse(text: str) -> dict[str, str]:
    """Markdown into {section: body}, with the preamble as `all`."""
    out: dict[str, str] = {}
    matches = list(HEADING.finditer(text))
    preamble = text[:matches[0].start()] if matches else text
    if preamble.strip():
        out[ALL] = preamble.strip()
    for n, match in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        name = match.group(1).strip().lower()
        body = text[match.end():end].strip()
        if not body:
            continue
        out[name] = f"{out[name]}\n\n{body}" if name in out else body
    return out


@dataclass
class House:
    """What the standards say, and where they came from."""

    sections: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    sha256: str = ""

    def for_stage(self, stage: str) -> str:
        """The block this stage is held to, or "" when there is nothing."""
        parts = [self.sections[name] for name in (ALL, stage)
                 if self.sections.get(name)]
        if not parts:
            return ""
        return ("The standards this channel holds every video to. They bind "
                "what you write here, and the creator's own notes for this run "
                "outrank them:\n\n" + "\n\n".join(parts))

    def digest(self) -> dict:
        return {"sources": self.sources, "sha256": self.sha256,
                "sections": sorted(self.sections)}


def files(work: Path | None, project_root: Path | None,
          explicit: str | None = None) -> list[Path]:
    """Channel first, then the project, then whatever was named."""
    out = []
    for candidate in ([work / FILENAME] if work else []) \
            + ([project_root / FILENAME] if project_root else []) \
            + ([Path(explicit).expanduser()] if explicit else []):
        if candidate.is_file() and candidate not in out:
            out.append(candidate)
    return out


def load(work: Path | None = None, project_root: Path | None = None, *,
         explicit: str | None = None, enabled: bool = True) -> House:
    if not enabled:
        return House()
    sections: dict[str, str] = {}
    sources, digest = [], hashlib.sha256()
    for path in files(work, project_root, explicit):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # A later file replaces a section by name and leaves the rest alone.
        sections.update(parse(text))
        sources.append(str(path))
        digest.update(text.encode("utf-8"))
    return House(sections=sections, sources=sources,
                 sha256=digest.hexdigest()[:16] if sources else "")


def for_args(args, project=None, stage: str = "") -> tuple[str, dict]:
    """The block and the digest for one stage, from the parsed arguments."""
    from . import workspace

    work = workspace.work_dir(args) if getattr(args, "work", None) else None
    root = getattr(project, "root", None)
    house = load(work, root, explicit=getattr(args, "house", None),
                 enabled=not getattr(args, "no_house", False))
    return house.for_stage(stage or ""), house.digest()
