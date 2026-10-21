"""docs/compute.md states what travels to a host and what doctor says about one."""
import re
import unittest
from pathlib import Path

from ytstudio import render

DOC = (Path(__file__).resolve().parent.parent / "docs" / "compute.md").read_text(encoding="utf-8")
FLAT = re.sub(r"\s+", " ", DOC)


class ComputeDoc(unittest.TestCase):
    def test_every_mirror_exclusion_is_listed(self):
        for pattern in render.SYNC_EXCLUDES:
            self.assertIn(f"`{pattern}`", DOC, pattern)

    def test_every_pull_back_pattern_is_listed(self):
        for pattern in render.PULL_BACK:
            self.assertIn(f"`{pattern}`", DOC, pattern)

    def test_the_lists_are_pointed_at_by_name(self):
        for name in ("SYNC_EXCLUDES", "PULL_BACK"):
            self.assertIn(name, DOC)

    def test_detached_jobs_are_described(self):
        for needle in ("nohup setsid", "re-attach"):
            self.assertIn(needle, FLAT, needle)

    def test_doctors_host_states_and_fixes_are_named(self):
        for needle in ("accepts TCP on the ssh port but not SSH (<error>); jobs sent there would hang",
                       "restart sshd on that host, or take it out of RS_RENDER_HOSTS",
                       "check ssh access, or unpack a release under ~/render on that host"):
            self.assertIn(needle, FLAT, needle)


if __name__ == "__main__":
    unittest.main()
