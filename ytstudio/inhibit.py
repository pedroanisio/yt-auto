"""Keep the machine awake while a stage waits on a model or a render host.

A stage blocked on the network looks idle, so the desktop suspends after its
idle timeout and the wait is lost. `held` takes a systemd sleep+idle inhibitor
for the life of the `with` block. The lock belongs to a helper process that
lives while its stdin is open, so closing the pipe releases it, and so does
this process dying by any means.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import contextmanager

TOOL = "systemd-inhibit"
RELEASE_WAIT = 5  # seconds the helper gets to exit before it is killed


def argv(why: str) -> list[str]:
    return [TOOL, "--what=sleep:idle", "--who=yt", f"--why={why}",
            "--mode=block", "cat"]


@contextmanager
def held(why: str):
    """Hold a sleep inhibitor for the block; carry on without one if it fails."""
    proc = None
    if shutil.which(TOOL):
        try:
            proc = subprocess.Popen(argv(why), stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as err:
            print(f"warning: no sleep inhibitor ({err})", file=sys.stderr)
    else:
        print(f"warning: {TOOL} not found; the machine may suspend mid-stage",
              file=sys.stderr)
    try:
        yield proc
    finally:
        if proc is not None:
            _release(proc)


def _release(proc) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=RELEASE_WAIT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
