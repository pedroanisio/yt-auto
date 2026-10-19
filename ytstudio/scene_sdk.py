"""Find `sr_core.scene`, installed or in a checkout beside this one.

sr-core is standard-library only, so a checkout works as well as an install:
the schema, the generated classes and the checks all come from the same files
the engines vendor.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

HINT = ("sr-core was not found. Either `pip install sr-core`, or keep the "
        "checkout beside this one, or set SR_CORE to it.")


def sdk():
    """The `sr_core.scene` module."""
    try:
        return importlib.import_module("sr_core.scene")
    except ImportError:
        pass
    here = Path(__file__).resolve().parent.parent
    tried = []
    for candidate in ([Path(os.environ["SR_CORE"])] if os.environ.get("SR_CORE")
                      else []) + [here.parent / "sr-core", Path.home() / "src/sr-core"]:
        tried.append(str(candidate))
        if (candidate / "sr_core" / "scene").is_dir():
            sys.path.insert(0, str(candidate))
            try:
                return importlib.import_module("sr_core.scene")
            except ImportError:
                sys.path.pop(0)
    raise RuntimeError(f"{HINT} Tried: {', '.join(tried)}")


def enum_of(element: str, attribute: str) -> list[str]:
    """The values the schema allows for one attribute, or [] when it is free."""
    try:
        described = sdk().describe(element).to_dict()
    except Exception:
        return []
    for attr in described.get("attributes", []):
        if attr.get("xml_name") == attribute:
            kind = attr.get("type")
            if isinstance(kind, dict):
                return list(kind.get("enum") or [])
    return []
