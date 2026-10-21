"""Lets a test exercise the remote code paths against a patched subprocess.

The suite runs with YT_NO_REMOTE set, so ytstudio refuses every ssh/rsync launch.
A test that replaces subprocess.run with a fake and wants to see the argv the
remote path would run lifts the refusal for itself with `fake_remote`; it must
patch subprocess.run in the same breath, or the real ssh is reachable again.
"""

from __future__ import annotations

import os
from unittest import mock

#: The host variables a faked remote must not inherit: env.load() (called by
#: freesound.token(), for one) copies the real hosts from the key files into
#: os.environ, so a test that fakes ssh would otherwise probe them.
HOST_VARS = ("RS_RENDER_HOSTS", "RS_RENDER_HOST")


def _only_named_hosts(env: dict) -> None:
    for name in HOST_VARS:
        os.environ.pop(name, None)
    os.environ.update(env)


def fake_remote(case, **env: str) -> None:
    """For one TestCase: YT_NO_REMOTE off (and `env` set), restored on cleanup."""
    patcher = mock.patch.dict(os.environ, {})
    patcher.start()
    _only_named_hosts(env)
    os.environ.pop("YT_NO_REMOTE", None)
    case.addCleanup(patcher.stop)


def fake_remote_module(module) -> None:
    """For setUpModule: the same, restored when the module's tests are done."""
    import unittest
    patcher = mock.patch.dict(os.environ, {})
    patcher.start()
    _only_named_hosts({})
    os.environ.pop("YT_NO_REMOTE", None)
    unittest.addModuleCleanup(patcher.stop)


def fake_remote_class(cls) -> None:
    """For setUpClass: the same, restored by cls.doClassCleanups."""
    patcher = mock.patch.dict(os.environ, {})
    patcher.start()
    _only_named_hosts({})
    os.environ.pop("YT_NO_REMOTE", None)
    cls.addClassCleanup(patcher.stop)
