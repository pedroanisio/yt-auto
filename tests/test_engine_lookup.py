"""Where `render.engine()` looks for the scene-render binary.

A developer's `cargo build --release` beside the checkout, a release archive
from GitHub Releases unpacked beside it (newest version first), RS_RENDER_BIN
naming the binary or a directory that holds it. The filesystem is a temporary
directory and PATH is empty of the engine; nothing is run.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import render  # noqa: E402


class Lookup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for p in (mock.patch.dict(os.environ, {}, clear=False),
                  mock.patch.object(render.shutil, "which", return_value=None),
                  mock.patch.object(render, "local_roots", return_value=(self.tmp,))):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("RS_RENDER_BIN", None)

    def binary(self, relative: str) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"#!/bin/sh\n")
        return path

    def test_nothing_found_is_none(self):
        self.assertIsNone(render.engine())

    def test_rs_render_bin_names_the_binary(self):
        binary = self.binary("elsewhere/scene-render")
        with mock.patch.dict(os.environ, {"RS_RENDER_BIN": str(binary)}):
            self.assertEqual(render.engine(), binary)

    def test_rs_render_bin_may_name_the_unpacked_archive(self):
        binary = self.binary("dl/scene-render-v0.7.1-linux-x86_64/scene-render")
        with mock.patch.dict(os.environ, {"RS_RENDER_BIN": str(binary.parent)}):
            self.assertEqual(render.engine(), binary)
        with mock.patch.dict(os.environ, {"RS_RENDER_BIN": str(self.tmp / "dl")}):
            self.assertEqual(render.engine(), binary)

    def test_a_dangling_rs_render_bin_falls_through(self):
        beside = self.binary("scene-render-v0.7.1-linux-x86_64/scene-render")
        with mock.patch.dict(os.environ, {"RS_RENDER_BIN": str(self.tmp / "missing")}):
            self.assertEqual(render.engine(), beside)

    def test_the_cargo_build_in_the_checkout_is_found(self):
        built = self.binary("scene-render/target/release/scene-render")
        self.assertEqual(render.engine(), built)

    def test_a_release_archive_beside_the_checkout_is_found(self):
        unpacked = self.binary("scene-render-v0.7.1-linux-x86_64/scene-render")
        self.assertEqual(render.engine(), unpacked)

    def test_an_archive_inside_a_folder_of_archives_is_found(self):
        unpacked = self.binary("scene-render-release/scene-render-v0.7.1-linux-x86_64/scene-render")
        self.assertEqual(render.engine(), unpacked)

    def test_the_developers_build_wins_over_an_archive(self):
        built = self.binary("scene-render/target/release/scene-render")
        self.binary("scene-render-v9.9.9-linux-x86_64/scene-render")
        self.assertEqual(render.engine(), built)

    def test_the_newest_archive_wins_like_sort_v(self):
        self.binary("scene-render-v0.7.1-linux-x86_64/scene-render")
        newest = self.binary("scene-render-v0.10.0-linux-x86_64/scene-render")
        self.binary("scene-render-v0.9.2-linux-x86_64/scene-render")
        self.assertEqual(render.engine(), newest)

    def test_a_directory_named_like_the_binary_is_not_the_binary(self):
        (self.tmp / "scene-render-v0.7.1-linux-x86_64" / "scene-render").mkdir(parents=True)
        self.assertIsNone(render.engine())

    def test_the_hint_names_the_archive(self):
        self.assertIn("release archive", render.BUILD_HINT)
        self.assertIn("scene-render-vX-linux-x86_64", render.BUILD_HINT)


class Roots(unittest.TestCase):
    def test_home_and_opt_are_not_searched(self):
        # a binary left there would pull work off the render hosts (SKILL.md invariant 9)
        roots = render.local_roots()
        self.assertNotIn(Path.home(), roots)
        self.assertNotIn(Path("/opt"), roots)
        self.assertIn(Path(render.__file__).resolve().parent.parent.parent, roots)


DIAG = {"code": "R24-fill", "severity": "error", "label": "r", "path": "/scene/rect",
        "message": "@fill: url(#id) must name an element of paints."}
WARN = {"code": "W3", "severity": "warning", "message": "a light nothing uses"}

#: What engines since 0.6.0 print: one scene-render-cli/1 envelope, the files under result.
ENVELOPE = {"format": "scene-render-cli/1", "schema_version": "1.2.0", "command": "validate",
            "ok": False, "exit_code": 1, "diagnostics": [],
            "error": {"kind": "invalid", "message": "1 of 1 file(s) invalid", "codes": ["R24-fill"]},
            "result": {"valid": False, "files": [{"file": "x.scene.xml", "valid": False,
                                                  "errors": 1, "warnings": 1,
                                                  "diagnostics": [DIAG, WARN]}]}}

#: What engines before 0.6.0 printed: the files at the top.
BARE = {"valid": False, "files": [{"file": "x.scene.xml", "valid": False,
                                   "diagnostics": [DIAG, WARN]}]}


class ValidateReport(unittest.TestCase):
    """validate_findings reads the envelope of 0.6.0+ and the bare shape before it alike."""

    def test_the_envelope_yields_each_files_diagnostics(self):
        findings = render.validate_findings(ENVELOPE)
        self.assertEqual(findings, [
            "error[R24-fill] (r): @fill: url(#id) must name an element of paints.",
            "warning[W3]: a light nothing uses"])

    def test_the_bare_shape_yields_the_same(self):
        self.assertEqual(render.validate_findings(BARE), render.validate_findings(ENVELOPE))

    def test_plain_invalidity_is_not_reported_twice(self):
        # error.kind == "invalid" only summarises the files' own diagnostics
        self.assertFalse([f for f in render.validate_findings(ENVELOPE) if "invalid" in f])

    def test_a_usage_or_io_error_is_a_finding(self):
        report = {"format": "scene-render-cli/1", "ok": False, "exit_code": 2, "result": None,
                  "diagnostics": [], "error": {"kind": "io", "message": "cannot read x.scene.xml",
                                               "codes": []}}
        self.assertEqual(render.validate_findings(report), ["error[io]: cannot read x.scene.xml"])

    def test_envelope_level_diagnostics_are_kept_once(self):
        report = dict(ENVELOPE, diagnostics=[DIAG, {"code": "E9", "severity": "error",
                                                    "message": "about no file"}])
        findings = render.validate_findings(report)
        self.assertEqual(findings.count(
            "error[R24-fill] (r): @fill: url(#id) must name an element of paints."), 1)
        self.assertIn("error[E9]: about no file", findings)

    def test_a_valid_document_has_no_findings(self):
        report = dict(ENVELOPE, ok=True, exit_code=0, error=None,
                      result={"valid": True, "files": [{"file": "x", "valid": True,
                                                        "diagnostics": []}]})
        self.assertEqual(render.validate_findings(report), [])


class LocalValidate(unittest.TestCase):
    """engine_validate runs a local binary itself and reads its envelope; no sr-core in the loop."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def fake_engine(self, script: str) -> Path:
        binary = self.tmp / "scene-render"
        binary.write_text("#!/bin/sh\n" + script)
        binary.chmod(0o755)
        return binary

    def test_the_local_binary_is_run_with_no_assets_and_its_envelope_read(self):
        import json
        seen = self.tmp / "argv"
        binary = self.fake_engine(f"printf '%s\\n' \"$@\" > {seen}\n"
                                  f"cat <<'EOF'\n{json.dumps(ENVELOPE)}\nEOF\nexit 1\n")
        with mock.patch.object(render, "engine", return_value=binary):
            findings = render.engine_validate(b"<scene/>")
        argv = seen.read_text().split("\n")
        self.assertEqual(argv[:4], ["validate", "--format", "json", "--no-assets"])
        self.assertTrue(argv[4].endswith(".scene.xml"))
        self.assertFalse(Path(argv[4]).exists(), "the temporary document is removed")
        self.assertEqual(findings[0],
                         "error[R24-fill] (r): @fill: url(#id) must name an element of paints.")

    def test_a_valid_document_is_an_empty_list_not_none(self):
        binary = self.fake_engine('echo \'{"format":"scene-render-cli/1","ok":true,"exit_code":0,'
                                  '"result":{"valid":true,"files":[]},"diagnostics":[],"error":null}\'\n')
        with mock.patch.object(render, "engine", return_value=binary):
            self.assertEqual(render.engine_validate(b"<scene/>"), [])

    def test_an_engine_that_prints_no_report_is_an_engine_failed_error(self):
        binary = self.fake_engine("echo 'internal error: boom at x.rs:1' >&2\nexit 2\n")
        with mock.patch.object(render, "engine", return_value=binary):
            findings = render.engine_validate(b"<scene/>")
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].startswith("error: sdk:engine-failed"))
        self.assertIn("boom", findings[0])

    def test_an_engine_that_cannot_start_is_an_engine_failed_error(self):
        with mock.patch.object(render, "engine", return_value=self.tmp / "absent"):
            findings = render.engine_validate(b"<scene/>")
        self.assertTrue(findings and findings[0].startswith("error: sdk:engine-failed"))

    def test_an_engine_that_exits_0_with_no_report_is_not_a_pass(self):
        binary = self.fake_engine("exit 0\n")
        with mock.patch.object(render, "engine", return_value=binary):
            findings = render.engine_validate(b"<scene/>")
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].startswith("error: sdk:engine-failed"))

    def test_invalid_with_no_diagnostics_is_not_a_pass(self):
        binary = self.fake_engine('echo \'{"format":"scene-render-cli/1","ok":false,"exit_code":1,'
                                  '"result":{"valid":false,"files":[]},"diagnostics":[],'
                                  '"error":{"kind":"invalid","message":"0 of 1"}}\'\nexit 1\n')
        with mock.patch.object(render, "engine", return_value=binary):
            findings = render.engine_validate(b"<scene/>")
        self.assertEqual(len(findings), 1)
        self.assertIn("lists no diagnostics", findings[0])

    def test_no_binary_and_no_host_is_none(self):
        with mock.patch.object(render, "engine", return_value=None), \
                mock.patch.object(render, "remote_host", return_value=""):
            self.assertIsNone(render.engine_validate(b"<scene/>"))


if __name__ == "__main__":
    unittest.main()
