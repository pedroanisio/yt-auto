"""The account's standards, loaded into everything `ig` asks the model.

The same mechanism as ytstudio.house (whose parser this reuses): a rule
lives in one Markdown file, layered, and each stage is handed `## all` plus
the sections it reads, so a rule is written once and the digest of what a
stage was handed is recorded beside its output.

    igstudio/house/default.md          the shipped default
    $IG_HOME/house.md                  every account of this registry
    $IG_HOME/house/<account>.md        one account, overriding sections by name
    --house FILE                       somewhere else entirely
    --no-house                         ignore all of it for this run

Sections, matched by stage name:

    ## all        everything that asks the model
    ## hook       the first line of a caption or the opening of a piece
    ## caption    the body, hashtags and language of a caption
    ## cta        what the piece asks the viewer to do, and how
    ## alt        alt text
    ## reply      answers to comments

`caption` reads hook, caption, cta and alt in that order, so one house file
shapes the whole caption; `reply` reads only its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ytstudio.house import ALL, _sha, parse

FILENAME = "house.md"
DEFAULT = Path(__file__).resolve().parent / "house" / "default.md"
DEFAULT_SOURCE = "igstudio/house/default.md"
STAGES = (ALL, "hook", "caption", "cta", "alt", "reply")
READS = {"caption": ("hook", "caption", "cta", "alt")}


@dataclass
class House:
    sections: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    sha256: str = ""

    def for_stage(self, stage: str) -> str:
        parts = [self.sections.get(name, "") for name in (ALL, *READS.get(stage, (stage,)))]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        return ("The standards this account holds every post to. They bind what you "
                "write here, and the brief for this post outranks them:\n\n"
                + "\n\n".join(parts))

    def digest(self, stage: str = "") -> dict:
        out = {"sources": self.sources, "sha256": self.sha256, "sections": sorted(self.sections)}
        if stage:
            block = self.for_stage(stage)
            out |= {"stage": stage, "block_sha256": _sha(block) if block else ""}
        return out


def files(root: Path | None, account: str = "", explicit: str | None = None) -> list[Path]:
    """The shipped default, the registry's, the account's, then whatever was named."""
    out = []
    candidates = [DEFAULT]
    if root:
        candidates.append(root / FILENAME)
        if account:
            candidates.append(root / "house" / f"{account}.md")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for path in candidates:
        if path.is_file() and path not in out:
            out.append(path)
    return out


def load(root: Path | None, account: str = "", *, explicit: str | None = None,
         enabled: bool = True) -> House:
    if not enabled:
        return House()
    sections: dict[str, str] = {}
    sources = []
    for path in files(root, account, explicit):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        sections.update(parse(text))
        sources.append(DEFAULT_SOURCE if path == DEFAULT else str(path))
    sha = _sha(json.dumps(sections, sort_keys=True, ensure_ascii=False)) if sources else ""
    return House(sections=sections, sources=sources, sha256=sha)
