#!/usr/bin/env python3
"""tail_check MEDIA --voice-end T [--scene SCENE] [--out FILE] [--label NAME]: a film's ending as it
sounds in the encoded file (no held tone, no glide, a decay, only soft struck effects after the
last word). Prints one line; exit 0 PASS, 1 CHECK, 2 could not measure.

Standard library and ffmpeg only. The same measured rules and constants as pnkit's
tools/tail_check.py; sr_audio.py beside this file holds them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from sr_audio import tail_check_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(tail_check_main())
