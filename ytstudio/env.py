"""Load API keys from a file, without ever printing or storing one.

Keys are read into the process environment only, and only when the variable is
not already set, so an explicit `export` always wins over a file. Nothing here
returns a value: the callers get the names, which is all they need to report.

Searched in order, first hit per variable wins:

    the path given to --env-file
    $YT_ENV_FILE
    ./_tmp/do_not_delete.env      (this checkout's own, untracked)
    ./.env
    ~/.config/pals-notes/.env     (where super-tools' kits look)
"""

from __future__ import annotations

import os
from pathlib import Path

CANDIDATES = ("_tmp/do_not_delete.env", ".env", "~/.config/pals-notes/.env")

#: Variables worth mentioning when they turn up, and what they unlock.
KNOWN = {
    "ELEVENLABS_API_KEY": "yt voice --engine elevenlabs",
    "FREESOUND_CLIENT_ID": "sound effects from Freesound (yt sound --fetch)",
    "FREESOUND_CLIENT_SECRET": "sound effects from Freesound (yt sound --fetch)",
    "FREESOUND_API_KEY": "sound effects from Freesound (older name of the secret)",
    "OPENAI_API_KEY": "third-party models",
}


def parse(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, ignoring comments, blanks and `export` prefixes."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, _, value = line.partition("=")
        name = name.strip()
        if name.isidentifier():
            out[name] = value.strip().strip("'\"")
    return out


def files(explicit: str | None = None) -> list[Path]:
    paths = []
    for raw in ([explicit] if explicit else []) + [os.environ.get("YT_ENV_FILE")] \
            + list(CANDIDATES):
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_file() and path not in paths:
            paths.append(path)
    return paths


def load(explicit: str | None = None) -> list[tuple[str, str]]:
    """Set what is missing from the environment. Returns (name, file) pairs."""
    loaded: list[tuple[str, str]] = []
    for path in files(explicit):
        for name, value in parse(path).items():
            if name in os.environ and os.environ[name].strip():
                continue
            if not value:
                continue
            os.environ[name] = value
            loaded.append((name, str(path)))
    return loaded
