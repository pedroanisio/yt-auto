"""The YouTube production pipeline, from an idea to a rendered cut.

Each stage is a module with a `register(subparsers)` / `run(args)` pair, so the
one `yt` command owns the argument parsing and the modules own the work. The
domain classes -- Generator, Researcher, Director, Screenwriter, VoiceOver,
VfxPlanner, SoundDesigner, SceneBuilder -- are importable on their own if you
would rather drive them from Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: claude_engine.py is a standalone library that sits beside this package, so
#: the directory holding both has to be importable however `yt` was started.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

__all__ = ["assets", "build", "cli", "core", "env", "ideas", "render",
           "research", "retime", "scene_sdk", "script", "sound", "styles",
           "vfx", "voice", "workspace"]
