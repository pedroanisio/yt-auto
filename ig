#!/usr/bin/env python3
"""ig -- manage several Instagram accounts. Run `./ig --help` to start.

A thin launcher: it makes this directory importable so `igstudio`,
`ytstudio` and `claude_engine` are found however and from wherever it is
called.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from igstudio.cli import main  # noqa: E402

raise SystemExit(main())
