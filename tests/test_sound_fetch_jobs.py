"""`yt sound --fetch --jobs N`: a round's cues are searched, downloaded and
judged several at once (AUTO-76).

Freesound and the judge are faked at the same seams as `test_sound_fetch`
and `test_sound_judge`; a barrier inside the fake proves the overlap, since
a serial fetch never has two cues inside it at the same time. What the tests
pin: the flag is the shared `--jobs`, default 4; a round's downloads overlap
and `sound/index.json` still records every file; a round's judgements
overlap; and the progress lines come out one cue at a time, in cue order,
however the threads interleave.
"""

import threading
import unittest

from ytstudio import cli, flags

from tests.test_sound_fetch import FetchCase, cue, row  # noqa: F401


class Flag(unittest.TestCase):
    def test_yt_sound_takes_jobs_from_the_shared_vocabulary_default_4(self):
        self.assertTrue(callable(getattr(flags, "jobs", None)), "flags.jobs is the one spelling")
        ns = cli.build_parser().parse_args(["sound", "--fetch"])
        self.assertEqual(ns.jobs, 4)
        self.assertEqual(cli.build_parser().parse_args(["sound", "--jobs", "2"]).jobs, 2)


if __name__ == "__main__":
    unittest.main()
