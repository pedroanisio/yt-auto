"""`yt doctor` and the claude and codex CLIs' route through Vercel AI Gateway
(AUTO-122).

With YT_GATEWAY set, the environment check carries a "gateway" block: a
missing key fails, a hand-set ANTHROPIC_BASE_URL that yt will replace (or
that ends in /v1, which the gateway answers with a 404) is a warning, a
parent ANTHROPIC_API_KEY the claude CLI's child will not see is a note, and
a note names the endpoints in use. With the switch unset there is no block.

`yt doctor --gateway-ping` asks GET /v1/credits for the team's balance. It is
the only network call doctor makes on the gateway's behalf, and only with
the flag. No test here reaches the network: urlopen is replaced in each.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, doctor  # noqa: E402

KEY = "vck_7Qz9XwL2pR4tY8mN0bV6cJ3hGk5D"

#: Every variable the gateway block or the ping reads, cleared for each test
#: so the developer's own shell cannot change the outcome.
CLEARED = ("YT_GATEWAY", "YT_GATEWAY_AUTH", "YT_GATEWAY_URL", "AI_GATEWAY_API_KEY",
           "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
           "CLAUDE_CODE_USE_FOUNDRY", "YT_NO_REMOTE")


def environ(**env) -> mock._patch_dict:
    patcher = mock.patch.dict(os.environ, {})
    patcher.start()
    for name in CLEARED:
        os.environ.pop(name, None)
    os.environ.update(env)
    return patcher


class Answer(io.BytesIO):
    """What urlopen hands back: a readable body that is a context manager."""

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(doctor.gateway.DEFAULT_HOST + "/v1/credits", code,
                                  "refused", Message(), io.BytesIO(body))


def never(*args, **kwargs):
    raise AssertionError("doctor reached the network")


class Block(unittest.TestCase):
    def rows(self, **env) -> list:
        patcher = environ(**env)
        self.addCleanup(patcher.stop)
        report = doctor.Report("environment")
        with mock.patch.object(urllib.request, "urlopen", side_effect=never):
            doctor.check_gateway(report)
        return [f for f in report.findings if f.area == "gateway"]

    def test_doctor_no_gateway_section_when_off(self):
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows(AI_GATEWAY_API_KEY=KEY), [])
        self.assertEqual(self.rows(YT_GATEWAY="off", AI_GATEWAY_API_KEY=KEY,
                                   ANTHROPIC_BASE_URL="https://example.com/v1"), [])

    def test_doctor_fails_missing_key(self):
        rows = self.rows(YT_GATEWAY="vercel")
        fails = [f for f in rows if f.level == "fail"]
        self.assertEqual(len(fails), 1, [f.message for f in rows])
        self.assertIn("AI_GATEWAY_API_KEY", fails[0].message)
        self.assertIn("AI_GATEWAY_API_KEY", fails[0].fix)
        # no endpoint is named for a route that cannot run
        self.assertFalse([f for f in rows if f.level == "ok"])

    def test_an_unknown_switch_or_a_cloud_switch_fails(self):
        rows = self.rows(YT_GATEWAY="vercell", AI_GATEWAY_API_KEY=KEY)
        self.assertEqual([f.level for f in rows if f.level == "fail"], ["fail"])
        self.assertIn("vercel", rows[0].message)
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         CLAUDE_CODE_USE_BEDROCK="1")
        self.assertTrue([f for f in rows if f.level == "fail"
                         and "CLAUDE_CODE_USE_BEDROCK" in f.message])

    def test_the_endpoints_in_use_are_named(self):
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY)
        self.assertEqual([f.level for f in rows], ["ok"], [f.message for f in rows])
        self.assertIn("https://ai-gateway.vercel.sh/claude-code", rows[0].message)
        self.assertIn("https://ai-gateway.vercel.sh/codex/v1", rows[0].message)
        self.assertIn("key", rows[0].message)

    def test_a_proxy_is_named_without_its_credentials(self):
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         YT_GATEWAY_URL="https://user:hunter2@proxy.example")
        said = " ".join(f.message + f.fix for f in rows)
        self.assertIn("https://proxy.example/claude-code", said)
        self.assertNotIn("hunter2", said)

    def test_doctor_warns_v1_suffix(self):
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         ANTHROPIC_BASE_URL="https://ai-gateway.vercel.sh/v1")
        warns = [f for f in rows if f.level == "warn"]
        self.assertEqual(len(warns), 1, [f.message for f in rows])
        self.assertIn("/v1", warns[0].message)
        self.assertIn("404", warns[0].message)
        self.assertIn("ANTHROPIC_BASE_URL", warns[0].fix)

    def test_a_hand_set_base_url_that_differs_is_a_warning(self):
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         ANTHROPIC_BASE_URL="https://llm.example/anthropic")
        warns = [f for f in rows if f.level == "warn"]
        self.assertEqual(len(warns), 1, [f.message for f in rows])
        self.assertIn("ANTHROPIC_BASE_URL", warns[0].message)
        self.assertIn("YT_GATEWAY_URL", warns[0].fix)
        # the computed one, with or without a trailing slash, is not
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         ANTHROPIC_BASE_URL="https://ai-gateway.vercel.sh/claude-code/")
        self.assertFalse([f for f in rows if f.level == "warn"])

    def test_doctor_notes_blanked_api_key(self):
        rows = self.rows(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                         ANTHROPIC_API_KEY="sk-ant-api03-Zq8Lw2Xp9Rv4Tn6")
        notes = [f for f in rows if f.level == "note"]
        self.assertEqual(len(notes), 1, [f.message for f in rows])
        self.assertIn("ANTHROPIC_API_KEY", notes[0].message)
        self.assertIn("blank", notes[0].message)

    def test_subscription_mode_warns_that_a_direct_key_is_sent(self):
        rows = self.rows(YT_GATEWAY="vercel", YT_GATEWAY_AUTH="subscription",
                         AI_GATEWAY_API_KEY=KEY, ANTHROPIC_API_KEY="sk-ant-api03-Zq8Lw2Xp9Rv4Tn6")
        warns = [f for f in rows if f.level == "warn"]
        self.assertEqual(len(warns), 1, [f.message for f in rows])
        self.assertIn("ANTHROPIC_API_KEY", warns[0].message)
        self.assertIn("unset ANTHROPIC_API_KEY", warns[0].fix)
        self.assertIn("subscription", rows[0].message)

    def test_check_environment_carries_the_block_and_the_known_key(self):
        # AI_AGENT shares no service with the gateway key: AI_ is no prefix
        patcher = environ(YT_GATEWAY="vercel", AI_AGENT="some-agent")
        self.addCleanup(patcher.stop)
        with mock.patch.object(doctor.render, "hosts", return_value=[]), \
                mock.patch.object(urllib.request, "urlopen", side_effect=never):
            report = doctor.check_environment(Path(tempfile.gettempdir()))
        self.assertTrue([f for f in report.findings
                         if f.area == "gateway" and f.level == "fail"])
        keys = [f.message for f in report.findings if f.area == "keys"]
        self.assertTrue([m for m in keys if m.startswith("AI_GATEWAY_API_KEY not set")
                         and "YT_GATEWAY=vercel" in m], keys)


class Ping(unittest.TestCase):
    def setUp(self):
        patcher = environ(AI_GATEWAY_API_KEY=KEY)
        self.addCleanup(patcher.stop)
        self.calls = []

    def ping(self, answer) -> list:
        def fake(request, timeout=None):
            self.calls.append((request, timeout))
            if isinstance(answer, BaseException):
                raise answer
            return Answer(answer)
        report = doctor.Report("environment")
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake):
            doctor.gateway_ping(report)
        self.report = report
        return [f for f in report.findings if f.area == "gateway ping"]

    def test_ping_parses_credits(self):
        rows = self.ping(json.dumps({"balance": "95.50", "total_used": "4.50"}).encode())
        self.assertEqual([f.level for f in rows], ["ok"])
        self.assertIn("95.50", rows[0].message)
        self.assertIn("4.50", rows[0].message)
        self.assertIn("team", rows[0].message)
        self.assertEqual(self.report.metrics["gateway_credits"],
                         {"balance": "95.50", "total_used": "4.50"})
        request, timeout = self.calls[0]
        self.assertEqual(request.full_url, "https://ai-gateway.vercel.sh/v1/credits")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        self.assertEqual(timeout, 5)

    def test_the_ping_goes_to_the_named_host(self):
        os.environ["YT_GATEWAY_URL"] = "https://proxy.example/"
        self.ping(b'{"balance": "1", "total_used": "0"}')
        self.assertEqual(self.calls[0][0].full_url, "https://proxy.example/v1/credits")

    def test_ping_reports_http_error_status_and_type(self):
        rows = self.ping(http_error(401, json.dumps(
            {"error": {"type": "authentication_error", "message": "Invalid API key"}}).encode()))
        self.assertEqual([f.level for f in rows], ["fail"])
        self.assertIn("401", rows[0].message)
        self.assertIn("authentication_error", rows[0].message)
        self.assertIn("API Keys", rows[0].fix)
        self.assertIn("https://vercel.com/", rows[0].fix)
        rows = self.ping(http_error(403, b"not json"))
        self.assertEqual([f.level for f in rows], ["fail"])
        self.assertIn("403", rows[0].message)
        self.assertIn("API Keys", rows[0].fix)
        rows = self.ping(http_error(404, b'{"error": {"type": "not_found"}}'))
        self.assertEqual([f.level for f in rows], ["fail"])
        self.assertIn("not_found", rows[0].message)
        # a busy or failing gateway is not the operator's configuration
        for code in (429, 503):
            rows = self.ping(http_error(code, b'{"error": {"type": "rate_limit_exceeded"}}'))
            self.assertEqual([f.level for f in rows], ["warn"], code)
            self.assertIn(str(code), rows[0].message)

    def test_an_unreachable_gateway_or_a_strange_answer_is_said(self):
        rows = self.ping(urllib.error.URLError("Name or service not known"))
        self.assertEqual([f.level for f in rows], ["warn"])
        self.assertIn("Name or service not known", rows[0].message)
        rows = self.ping(TimeoutError("timed out"))
        self.assertEqual([f.level for f in rows], ["warn"])
        rows = self.ping(b"<html>")
        self.assertEqual([f.level for f in rows], ["warn"])
        rows = self.ping(b'{"credits": 3}')
        self.assertEqual([f.level for f in rows], ["warn"])

    def test_no_key_is_refused_without_a_call(self):
        os.environ.pop("AI_GATEWAY_API_KEY")
        rows = self.ping(b"{}")
        self.assertEqual([f.level for f in rows], ["fail"])
        self.assertIn("AI_GATEWAY_API_KEY", rows[0].message)
        self.assertEqual(self.calls, [])

    def test_a_key_with_a_newline_inside_is_refused_without_a_call(self):
        # in a header a newline would start another header
        os.environ["AI_GATEWAY_API_KEY"] = KEY[:10] + "\nX-Other: 1"
        rows = self.ping(b"{}")
        self.assertEqual([f.level for f in rows], ["fail"])
        self.assertEqual(self.calls, [])
        self.assertNotIn(KEY[:10], rows[0].message + rows[0].fix)

    def test_the_suite_switch_refuses_the_ping(self):
        os.environ["YT_NO_REMOTE"] = "1"
        rows = self.ping(b"{}")
        self.assertEqual([f.level for f in rows], ["warn"])
        self.assertIn("YT_NO_REMOTE", rows[0].message)
        self.assertEqual(self.calls, [])


class Command(unittest.TestCase):
    """Through `yt doctor` itself: the flag, and what is printed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doctor(self, *argv, answer=None) -> tuple[str, list]:
        calls = []

        def fake(request, timeout=None):
            calls.append(request)
            if isinstance(answer, BaseException):
                raise answer
            return Answer(answer or b"{}")
        args = cli.build_parser().parse_args(
            ["doctor", "--work", self.tmp.name, "--env", *argv])
        out = io.StringIO()
        with mock.patch.object(doctor.render, "hosts", return_value=[]), \
                mock.patch.object(urllib.request, "urlopen", side_effect=fake), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            args.run(args)
        return out.getvalue(), calls

    def test_ping_not_run_without_flag(self):
        patcher = environ(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY)
        self.addCleanup(patcher.stop)
        printed, calls = self.doctor()
        self.assertEqual(calls, [])
        self.assertNotIn("gateway ping", printed)
        printed, calls = self.doctor("--gateway-ping",
                                     answer=b'{"balance": "12.00", "total_used": "3.25"}')
        self.assertEqual(len(calls), 1)
        self.assertIn("12.00", printed)

    def test_doctor_never_prints_key(self):
        patcher = environ(YT_GATEWAY="vercel", AI_GATEWAY_API_KEY=KEY,
                          ANTHROPIC_API_KEY="sk-ant-api03-Zq8Lw2Xp9Rv4Tn6",
                          ANTHROPIC_BASE_URL="https://ai-gateway.vercel.sh/v1")
        self.addCleanup(patcher.stop)
        out = Path(self.tmp.name) / "health.json"
        printed = ""
        for answer in (b'{"balance": "1.00", "total_used": "2.00"}',
                       http_error(401, json.dumps({"error": {
                           "type": "authentication_error",
                           "message": f"Invalid API key {KEY}"}}).encode()),
                       urllib.error.URLError("refused")):
            text, _ = self.doctor("--gateway-ping", "--json", str(out), answer=answer)
            printed += text + out.read_text()
        for secret in (KEY, "sk-ant-api03-Zq8Lw2Xp9Rv4Tn6"):
            for n in range(len(secret) - 5):
                self.assertNotIn(secret[n:n + 6], printed)


if __name__ == "__main__":
    unittest.main()
