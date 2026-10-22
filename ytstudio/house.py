"""The channel's standards, loaded into every stage that writes.

A rule like "the narration has to carry the message on first listen" binds the
screenplay, the visual direction, the animation plan and the sound at once.
Pasting it into each stage's SYSTEM prompt would put one rule in five places
that then drift, and passing it as --notes retypes it every run. So it lives in
a file, layered:

    ytstudio/house/default.md   the shipped default, distilled from docs/narrative/
    work/house.md               the channel's standard
    work/<slug>/house.md        this video's own, overriding sections by name
    --house FILE                somewhere else entirely
    --no-house                  ignore all of it for this run

Sections are Markdown headings, matched by stage name, and each stage is given
`## all` plus its own:

    ## all          every stage that asks the model
    ## script       the screenplay, after `## format`
    ## styles       the visual direction
    ## vfx          the animation plan
    ## sound        effects, score and mix
    ## voice        the read
    ## format       the channel's recurring narrator, opening, ending and
                    hand-off, which the screenplay follows

Anything before the first heading counts as `## all`, and an HTML comment is
the maintainer's, never the model's: the default's comments name the corpus
rules each section came from. A later file's section replaces the earlier
file's section of the same name; sections it does not mention still apply.

Every decision's cache digest carries the house digest, so a changed default
is a changed question: editing default.md re-asks every cached decision in
every project on its next run, and `yt doctor` warns about a manifest decided
under a different house digest.

The order a stage sees is deliberate: its SYSTEM prompt says what it is, these
standards say how this channel works, the dossier and the direction say what is
true of this video, and --notes outranks all of it for one run. The digest of
what was loaded goes into the stage's manifest, so an artifact says which
standards produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "house.md"
HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
COMMENT = re.compile(r"<!--.*?-->", re.S)
ALL = "all"

#: The shipped default, under every other house file.
DEFAULT = Path(__file__).resolve().parent / "house" / "default.md"
#: How a digest names it: the same in every checkout, so a manifest decided
#: under the default says so wherever it was made.
DEFAULT_SOURCE = "ytstudio/house/default.md"

#: The sections the default ships, in its order. A stage reads `## all` and
#: the sections READS names for it, else its own.
STAGES = (ALL, "script", "styles", "vfx", "sound", "voice", "format")

#: A stage that reads more than its own section, in the order it reads them.
#: The format is the channel's frame around every screenplay, so the writer
#: reads it before the script rules.
READS = {"script": ("format", "script")}

#: A stage with no section of its own reads another's. The keyframe authors
#: the lead section's animation, the look the vfx stage then follows, so
#: until a house writes `## keyframe` it is held to `## vfx`.
FALLBACK = {"keyframe": "vfx"}


def parse(text: str) -> dict[str, str]:
    """Markdown into {section: body}, with the preamble as `all`. Comments
    are dropped: they are provenance for whoever edits the file."""
    text = COMMENT.sub("", text)
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
        parts = [self.sections.get(name) or self.sections.get(FALLBACK.get(name, ""), "")
                 for name in (ALL, *READS.get(stage, (stage,)))]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        return ("The standards this channel holds every video to. They bind "
                "what you write here, and the creator's own notes for this run "
                "outrank them:\n\n" + "\n\n".join(parts))

    def digest(self, stage: str = "") -> dict:
        """What was loaded, and with a stage, the digest of the block that
        stage is handed. The block is what a decision's cache key holds, so
        `block_sha256` is what tells doctor whether the stage asks again."""
        out = {"sources": self.sources, "sha256": self.sha256,
               "sections": sorted(self.sections)}
        if stage:
            block = self.for_stage(stage)
            out |= {"stage": stage, "block_sha256": _sha(block) if block else ""}
        return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def files(work: Path | None, project_root: Path | None,
          explicit: str | None = None) -> list[Path]:
    """The shipped default, then the channel, then the project, then
    whatever was named."""
    out = []
    for candidate in [DEFAULT] + ([work / FILENAME] if work else []) \
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
    sources = []
    for path in files(work, project_root, explicit):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # A later file replaces a section by name and leaves the rest alone.
        sections.update(parse(text))
        sources.append(DEFAULT_SOURCE if path == DEFAULT else str(path))
    # The digest is of the sections a prompt can be handed, so an edit to a
    # provenance comment, or a section a later file replaces, changes nothing.
    sha = _sha(json.dumps(sections, sort_keys=True, ensure_ascii=False)) if sources else ""
    return House(sections=sections, sources=sources, sha256=sha)


def for_args(args, project=None, stage: str = "") -> tuple[str, dict]:
    """The block and the digest for one stage, from the parsed arguments."""
    from . import workspace

    work = workspace.work_dir(args) if getattr(args, "work", None) else None
    root = getattr(project, "root", None)
    house = load(work, root, explicit=getattr(args, "house", None),
                 enabled=not getattr(args, "no_house", False))
    return house.for_stage(stage or ""), house.digest(stage or "")
