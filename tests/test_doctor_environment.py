"""`yt doctor`'s environment check covers what a full run needs to finish, not
only what each stage needs to start: disk on the render hosts (the binding
constraint, SKILL.md "Rendering"), ffprobe on the host the gate runs on, and
the Python packages the stages import at runtime.

No test opens a connection: subprocess.run is replaced, and hosts are built.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import hermetic  # noqa: E402
from ytstudio import doctor, render  # noqa: E402


def done(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)


PROBE_OUT = ("ENGINE=/root/render/scene-render\nVERSION=scene-render 0.6.0\nSHA=abc\n"
             "RAM=125\nCORES=12\nGPU=NVIDIA GeForce RTX 3050\nGPUMEM=8192\n"
             "FFPROBE=/usr/bin/ffprobe\nDISK=14G\n")


class ProbeDisk(unittest.TestCase):
    """The probe asks each host how much disk is free where the mirrors live."""

    def setUp(self):
        hermetic.fake_remote(self)
        render._HOSTS.clear()
        self.addCleanup(render._HOSTS.clear)

    def probe(self, out=PROBE_OUT):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            return done(argv, 0, out)
        with mock.patch.object(render.subprocess, "run", side_effect=fake):
            host = render.probe("root@h")
        return host, calls

    def test_the_free_space_under_the_remote_root_is_read(self):
        host, calls = self.probe()
        self.assertEqual(host.disk_free_gb, 14)
        self.assertEqual(host.gpu_mem_mb, 8192)        # AUTO-40's field is untouched
        script = " ".join(calls[0])
        self.assertIn("DISK=", script)
        self.assertIn(str(Path(render.REMOTE_ROOT).parent), script)

    def test_a_host_that_does_not_say_reports_zero(self):
        host, _ = self.probe(PROBE_OUT.replace("DISK=14G\n", "DISK=\n"))
        self.assertEqual(host.disk_free_gb, 0)

    def test_parse_gb(self):
        self.assertEqual(render.parse_gb("14G"), 14)
        self.assertEqual(render.parse_gb("  1532G \n"), 1532)
        self.assertEqual(render.parse_gb("0G"), 0)
        self.assertEqual(render.parse_gb(""), 0)
        self.assertEqual(render.parse_gb("df: /tmp: No such file"), 0)


def _hosts(big_disk=100, small_disk=100, big_ffprobe="/usr/bin/ffprobe",
           small_ffprobe="/usr/bin/ffprobe"):
    big = render.Host("root@big", engine="/e", version="scene-render 0.6.0", ram_gb=125,
                      cores=12, gpu="RTX 3050", disk_free_gb=big_disk, ffprobe=big_ffprobe)
    small = render.Host("root@small", engine="/e", version="scene-render 0.6.0", ram_gb=15,
                        cores=20, gpu="RTX 3070", disk_free_gb=small_disk, ffprobe=small_ffprobe)
    return [big, small]


class HostDisk(unittest.TestCase):
    """Low disk on a render host is a warning before an encode, not a surprise after."""

    def report(self, hosts):
        report = doctor.Report("environment")
        doctor.check_hosts(report, hosts)
        return report

    def rows(self, report, *words):
        return [f for f in report.findings
                if all(w in f"{f.message} {f.fix}" for w in words)]

    def test_a_host_short_of_disk_is_a_warning_that_names_the_cure(self):
        report = self.report(_hosts(big_disk=doctor.LOW_DISK_GB - 1))
        warns = [f for f in self.rows(report, "root@big", "GB free") if f.level == "warn"]
        self.assertEqual(len(warns), 1, [f.message for f in report.findings])
        self.assertIn("--prune-remote", warns[0].fix)
        self.assertIn(str(doctor.LOW_DISK_GB - 1), warns[0].message)
        # the small host, with room, is not warned about
        self.assertFalse([f for f in self.rows(report, "root@small") if f.level == "warn"])

    def test_a_host_with_room_shows_it_on_its_ok_line(self):
        report = self.report(_hosts())
        ok = [f for f in self.rows(report, "root@big") if f.level == "ok"]
        self.assertEqual(len(ok), 1)
        self.assertIn("100 GB free", ok[0].message)
        self.assertEqual(report.count("warn"), 0)

    def test_a_host_that_did_not_say_gets_no_disk_finding(self):
        report = self.report(_hosts(big_disk=0, small_disk=0))
        self.assertFalse(self.rows(report, "GB free"))
        self.assertEqual(report.count("warn"), 0)


class HostFfprobe(unittest.TestCase):
    """The gate runs on the encode host and needs ffprobe there."""

    def report(self, hosts):
        report = doctor.Report("environment")
        doctor.check_hosts(report, hosts)
        return report

    def test_no_ffprobe_on_the_encode_host_warns_that_the_gate_cannot_run(self):
        report = self.report(_hosts(big_ffprobe=""))
        warns = [f for f in report.findings if f.level == "warn" and "ffprobe" in f.message]
        self.assertEqual(len(warns), 1, [f.message for f in report.findings])
        self.assertIn("root@big", warns[0].message)
        self.assertIn("yt gate", warns[0].message)
        self.assertTrue(warns[0].fix)

    def test_no_ffprobe_on_the_still_host_is_only_a_note(self):
        report = self.report(_hosts(small_ffprobe=""))
        rows = [f for f in report.findings if "ffprobe" in f.message]
        self.assertEqual([f.level for f in rows], ["note"])
        self.assertIn("root@small", rows[0].message)

    def test_ffprobe_everywhere_is_quiet(self):
        report = self.report(_hosts())
        self.assertFalse([f for f in report.findings if "ffprobe" in f.message])


class RuntimePackages(unittest.TestCase):
    """The packages the stages import at runtime are checked before a run, with
    the stage each one unlocks, so a missing one is not found by a traceback
    after the money upstream of it was spent."""

    def environment(self, missing=()):
        real = doctor.importlib.util.find_spec

        def find_spec(name, *a, **kw):
            return None if name in missing else real(name, *a, **kw)
        with mock.patch.object(render, "hosts", return_value=[]), \
                mock.patch.object(doctor.importlib.util, "find_spec", side_effect=find_spec):
            return doctor.check_environment(Path(tempfile.gettempdir()))

    def test_the_checked_packages_are_the_ones_the_stages_import(self):
        modules = {m for m, _, _ in doctor.RUNTIME_PACKAGES}
        self.assertEqual(modules, {"PIL", "numpy", "scipy"})

    def test_a_missing_package_is_a_warning_naming_the_stage_and_the_install(self):
        report = self.environment(missing={"scipy"})
        rows = [f for f in report.findings if f.area == "packages"]
        warns = [f for f in rows if f.level == "warn"]
        self.assertEqual(len(warns), 1, [f.message for f in rows])
        self.assertIn("scipy", warns[0].message)
        self.assertIn("yt sound --synth", warns[0].message)
        self.assertIn("pip install", warns[0].fix)

    def test_every_package_present_is_one_ok_line(self):
        report = self.environment()
        rows = [f for f in report.findings if f.area == "packages"]
        self.assertEqual([f.level for f in rows], ["ok"])
        for module, _, _ in doctor.RUNTIME_PACKAGES:
            self.assertIn(module, rows[0].message)


class RealEngines(unittest.TestCase):
    """doctor.real_engines: the narration engines usable here that may be
    published, which `yt improve` reads to tell a scratch take it can redo
    from one only an install can fix (AUTO-152)."""

    def engines(self, usable):
        from ytstudio import voice

        class Fake:
            def __init__(self, name):
                self.name = name

            def available(self):
                return (self.name in usable, "")
        return mock.patch.dict(voice.ENGINES, {n: Fake(n) for n in voice.ENGINES})

    def environment(self):
        with mock.patch.object(render, "hosts", return_value=[]):
            return doctor.check_environment(Path(tempfile.gettempdir()))

    def test_real_engines_excludes_scratch_engines(self):
        with self.engines({"stub", "espeak", "kokoro"}):
            self.assertEqual(doctor.real_engines(), ["kokoro"])
        with self.engines({"stub", "espeak"}):
            self.assertEqual(doctor.real_engines(), [])
        with self.engines({"stub", "elevenlabs", "kokoro"}):
            self.assertEqual(doctor.real_engines(), ["elevenlabs", "kokoro"])

    def test_the_voice_row_reads_the_same_probe(self):
        with self.engines({"stub", "espeak"}):
            row = next(f for f in self.environment().findings if f.area == "voice")
        self.assertEqual(row.level, "warn")
        self.assertIn("pip install kokoro, or set ELEVENLABS_API_KEY", row.fix)
        with self.engines({"stub", "kokoro"}):
            row = next(f for f in self.environment().findings if f.area == "voice")
        self.assertEqual(row.level, "ok")
        self.assertIn("engines usable: kokoro, stub", row.message)


if __name__ == "__main__":
    unittest.main()
