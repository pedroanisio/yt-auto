"""yt qa --listen: a model that hears the film's rendered audio (AUTO-126).

QA never heard the film: voiceover, sound_design and music_score are scored
only from a listening review (AUTO-105), and the two the pipeline had read
the cue sheet and the takes, never the mix. `--listen` is the opt-in pass
that hears the final track: the film's own audio, taken out by stream copy,
sent to an allowlisted model through the gateway engine, its findings
checked past the schema and filed as listening evidence.

The claude and codex CLIs stay the default. Without --listen every byte the
reviewer is sent, every cache key and every record is what it was, whatever
YT_GATEWAY says; with it, everything it needs is checked before a frame is
rendered or a pass directory is made.

Nothing here reaches a model, the network or a render host: the gateway
engine runs a fake `node` (tests/gateway_fakes.py), the scorecard reviewer
is the QA tests' fake engine, and `ffmpeg` and `ffprobe` are fakes on PATH
that log their argv (ffmpeg writes fixed bytes to its last argument, ffprobe
prints a set duration).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import gateway_engine  # noqa: E402
from claude_engine import Event, Usage  # noqa: E402
from gateway_fakes import MODEL, USAGE, FakeNode, err, ok  # noqa: E402
from tests import test_qa_listening as listening  # noqa: E402
from tests import test_qa_motion as motion  # noqa: E402
from ytstudio import cli, harness, models, qa, spend  # noqa: E402


def _listen():
    """The listening module, imported when a test needs it (it is new)."""
    return importlib.import_module("ytstudio.listen")


#: What today's reviewer was sent and filed, captured from the code before
#: --listen existed (the AUTO-105 stage test's project, the temporary paths
#: replaced by <root> and <work>). A deliberate change to QA's prompt or
#: records rewrites these; --listen left off never may. AUTO-106 rewrote the
#: prompt's: the "Previous pass, for comparison" brief (null on a first
#: pass) is gone, and that is the only difference from the digest before it.
#: AUTO-156 added qa.json's `judge_calibration`; the prompt and card are as before.
OFF = {
    "prompt_sha": "57bab3709bba64b6ead210e9a504a5cbb9e4990608e590d0a9194ea7bfc9714b",
    "role_sha": "fb112df18eac174eff86df8020f3ab2bd3d51f9bea23f0577196bf2efba22954",
    "card_keys": ["audio_not_evidenced", "bar", "blocking", "capped", "comparison",
                  "contact_sheet", "cut", "engine", "findings", "frames", "lines_unsampled",
                  "listening", "low_confidence", "mechanical", "not_evidenced", "overall",
                  "pass", "proof_note", "rationale", "samples", "scene_sha256", "scores",
                  "scoring", "side_by_side", "spread", "story", "unsampled", "usage",
                  "verdict"],
    "qa_keys": ["audio_not_evidenced", "blocking", "capped", "findings", "house", "idea",
                "judge_calibration", "low_confidence", "mechanical", "overall", "pass", "reasons", "regressions",
                "scene_sha256", "scorecard", "scores", "scoring", "side_by_side", "spread",
                "usage", "verdict"],
    "usage_keys": ["by_model", "calls", "cost_usd", "model", "unmetered_calls"],
}

FAKE_FFMPEG = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "ffmpeg.jsonl"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "audio.bin"), "rb") as f:
    data = f.read()
with open(sys.argv[-1], "wb") as f:
    f.write(data)
'''

FAKE_FFPROBE = r'''#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "ffprobe.jsonl"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "duration.txt")) as f:
    print(f.read().strip())
'''

#: The film's runtime as motion._build lays it out, and as the fake ffprobe says.
SECONDS = 16.5

FINDING = {"timestamp": 3.2, "problem": "the hit on the crash clips", "why": "it distorts",
           "root_cause": "cue S1 placed at -3 dBFS peak", "owner": "sound",
           "fix": "lower S1 by 6 dB", "severity": "blocking"}


def answer(findings=None, **categories) -> dict:
    """A listening answer that holds to LISTEN_SCHEMA; `findings` are sound_design's."""
    cats = {"voiceover": {"heard": "a clear, even read", "findings": []},
            "sound_design": {"heard": "the crash lands at 3 s",
                             "findings": [FINDING] if findings is None else findings},
            "music_score": {"heard": "a bed under the voice", "findings": []}}
    cats.update(categories)
    return {"heard": {"seconds": SECONDS, "narration": True, "music": True, "effects": True},
            "categories": cats, "not_heard": ["whether the voice is in sync with the lips"]}


class Recording(motion.FakeEngine):
    """The scorecard reviewer: the QA tests' fake, recording each system prompt
    and booking a fixed cost per inspection, so a merged usage can be read."""

    stream_calls: list = []
    json_calls: list = []
    systems: list = []

    def __init__(self, config):
        super().__init__(config)
        self.usage = Usage(cost_usd=0.25, calls=1, by_model={
            "claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 5, "costUSD": 0.25}})

    def ask_json(self, prompt, schema, system=None):
        Recording.json_calls.append(prompt)
        return dict(motion.SCORECARD)

    def stream(self, prompt, system=None):
        Recording.systems.append(system)
        Recording.stream_calls.append({"prompt": prompt, "cwd": self.config.cwd})
        yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                    tool_input={"file_path": "contact-sheet.png"})
        yield Event(type="assistant", text="```json\n" + json.dumps(motion.SCORECARD) + "\n```")
        yield Event(type="result", raw={})


class Project(unittest.TestCase):
    """A rendered film with current listen-back and take reviews, a fake
    reviewer and a fake renderer that counts its jobs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-listen-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.root = self.work / "one-take"
        saved = harness.ENGINE, qa.RENDERER, qa.GEOMETRY
        self.addCleanup(setattr, qa, "GEOMETRY", saved[2])
        self.addCleanup(setattr, qa, "RENDERER", saved[1])
        self.addCleanup(setattr, harness, "ENGINE", saved[0])
        harness.ENGINE = Recording
        Recording.stream_calls, Recording.json_calls, Recording.systems = [], [], []
        motion.FakeEngine.stream_calls, motion.FakeEngine.json_calls = [], []
        qa.GEOMETRY = lambda args, project, scene, shots: {"status": "skipped",
                                                          "why": "a test"}
        self.jobs = []

        def render(args, scene):
            def job(j):
                self.jobs.append(j)
                out = j["out"]
                a, b = (int(v) for v in j["frames"].split(".."))
                for f in range(a, b):
                    motion._square(Path(out.replace("%04d", f"{f:04d}")), 100 + f // 2)
                return 0
            return job
        qa.RENDERER = render
        self.make_project()

    def make_project(self, *, script=None, sound=None, voice=None, audio_tracks=3,
                     film=b"film-1"):
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        (self.root / "scene.xml").write_text("<scene/>")
        self.put("script.json", script if script is not None else {
            "engine": {"model": "sonnet", "by_model": {"claude-sonnet-5-5": {"costUSD": 1.0}}}})
        self.put("sound.json", sound if sound is not None else listening._sound())
        self.put("voice.json", voice if voice is not None else listening._voice())
        self.put("build.json", motion._build(audio_tracks=audio_tracks))
        (self.root / "video.mp4").write_bytes(film)
        later = (self.root / "scene.xml").stat().st_mtime + 60
        for name in ("build.json", "video.mp4"):
            os.utime(self.root / name, (later, later))

    def put(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def args(self, **kw):
        base = dict(project=str(self.root), title=None, index=None, source=None,
                    stdin=None, genre="short", description="one take", work=str(self.work),
                    scene=None, frame_limit=4, quality="preview", reuse=None,
                    previous=None, host=None, local=False, fresh=False, quiet=True,
                    model="sonnet", in_build=None, in_script=None, in_voice=None,
                    in_sound=None, in_styles=None, in_gate=None, house=None, no_house=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def run_qa(self, **kw):
        out, errs = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errs):
            code = qa.run(self.args(**kw))
        self.stdout, self.stderr = out.getvalue(), errs.getvalue()
        return code

    def passes(self) -> list[str]:
        qa_dir = self.root / "qa"
        return sorted(p.name for p in qa_dir.glob("pass-*")) if qa_dir.is_dir() else []

    def card(self, name="pass-01") -> dict:
        return json.loads((self.root / "qa" / name / "scorecard.json").read_text())

    def prompt(self) -> str:
        return Recording.stream_calls[-1]["prompt"]


class Listen(Project):
    """--listen with the gateway on and every prerequisite present."""

    def setUp(self):
        self.node = FakeNode(self)        # the key, a fake node, gateway/node_modules
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("ffmpeg", FAKE_FFMPEG), ("ffprobe", FAKE_FFPROBE)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        # the fakes' `#!/usr/bin/env python3`, for a PATH that holds the fakes alone
        (self.bin / "python3").symlink_to(sys.executable)
        (self.bin / "audio.bin").write_bytes(b"\xff\xf1" + b"aac" * 300)
        (self.bin / "duration.txt").write_text(f"{SECONDS}\n")
        env = mock.patch.dict(os.environ, {
            "YT_GATEWAY": "vercel", "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        env.start()
        self.addCleanup(env.stop)
        self.node.replies(ok(answer()))

    def args(self, **kw):
        kw.setdefault("listen", True)
        kw.setdefault("listen_model", MODEL)
        kw.setdefault("listen_budget_usd", 0.5)
        return super().args(**kw)

    def ffmpeg_calls(self) -> list[list[str]]:
        path = self.bin / "ffmpeg.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def ffprobe_calls(self) -> list[list[str]]:
        path = self.bin / "ffprobe.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def listening_json(self, name="pass-01") -> dict:
        return json.loads((self.root / "qa" / name / "listening.json").read_text())

    def refused(self, *needles, **kw):
        """--listen refused before anything is paid: exit 1, the piece named,
        no render job, no pass directory, nothing asked of the gateway (counted
        from the call, so a test may refuse after a run that listened)."""
        before, asked = self.passes(), self.node.count
        self.jobs.clear()
        Recording.stream_calls.clear()
        code = self.run_qa(**kw)
        self.assertEqual(code, 1, self.stderr)
        for needle in needles:
            self.assertIn(needle, self.stderr)
        self.assertIn("--listen", self.stderr)
        self.assertEqual(self.jobs, [], "a render job started")
        self.assertEqual(self.passes(), before, "a pass directory was made")
        self.assertEqual(self.node.count, asked, "the gateway was asked")
        self.assertEqual(Recording.stream_calls, [], "the reviewer was asked")


# --------------------------------------------------------------------------- #
# off: nothing changes
# --------------------------------------------------------------------------- #

class Off(Project):
    def normalized(self, prompt: str) -> str:
        return prompt.replace(str(self.root), "<root>").replace(str(self.work), "<work>")

    def digests(self) -> dict:
        return {p.name: json.loads(p.read_text())["digest"]
                for p in sorted((self.root / "decisions" / "qa").glob("*.json"))}

    def test_listen_off_is_identical(self):
        # an operator's old command line: no --listen attributes at all
        old = self.args()
        self.assertFalse(hasattr(old, "listen"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            qa.run(old)
        prompt, system, digests = self.prompt(), Recording.systems[-1], self.digests()
        card = self.card()
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(hashlib.sha256(self.normalized(prompt).encode()).hexdigest(),
                         OFF["prompt_sha"])
        self.assertEqual(hashlib.sha256(qa.ROLE.encode()).hexdigest(), OFF["role_sha"])
        self.assertIn(qa.ROLE, system)
        self.assertNotIn("listening.json", system)
        self.assertEqual(sorted(card), OFF["card_keys"])
        self.assertEqual(sorted(record), OFF["qa_keys"])
        self.assertEqual(sorted(card["usage"]), OFF["usage_keys"])
        self.assertEqual(card["usage"], spend.from_report(card["engine"]))
        self.assertFalse((self.root / "qa" / "listen").exists())
        self.assertFalse((self.root / "qa" / "pass-01" / "listening.json").exists())

        # The gateway switched on and its key set, the flag left off: the
        # same project asks exactly the same questions under the same keys.
        self.make_project()
        Recording.stream_calls, Recording.systems = [], []
        with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel",
                                          "AI_GATEWAY_API_KEY": "vck_test"}):
            self.run_qa(listen=False, listen_model=MODEL, listen_budget_usd=0.5)
        self.assertEqual(self.prompt(), prompt)
        self.assertEqual(Recording.systems[-1], system)
        self.assertEqual(self.digests(), digests)
        self.assertEqual(sorted(self.card()), OFF["card_keys"])
        self.assertFalse((self.root / "qa" / "listen").exists())

    def test_the_flags_parse_and_listen_is_off_by_default(self):
        parser = cli.build_parser()
        args = parser.parse_args(["qa", "--title", "x"])
        self.assertFalse(args.listen)
        self.assertEqual(args.listen_model, "google/gemini-3.6-flash")
        self.assertEqual(args.listen_budget_usd, 0.50)
        args = parser.parse_args(["qa", "--title", "x", "--listen", "--listen-model",
                                  "xiaomi/mimo-v2.6-pro", "--listen-budget-usd", "0.2"])
        self.assertTrue(args.listen)
        self.assertEqual(args.listen_model, "xiaomi/mimo-v2.6-pro")
        self.assertEqual(args.listen_budget_usd, 0.2)

    def test_every_listen_owner_is_a_qa_owner(self):
        # improve routes a finding by its owner alone; the listener's are QA's
        self.assertLessEqual(set(_listen().LISTEN_OWNERS), set(qa.OWNERS))
        self.assertEqual(_listen().LISTEN_SCHEMA["properties"]["categories"]["required"],
                         list(qa.AUDIO))

    def test_an_aac_stream_is_sent_as_audio_aac(self):
        self.assertEqual(gateway_engine.media_type("mix.aac"), "audio/aac")
        self.assertEqual(gateway_engine.media_type("mix.m4a"), "audio/mp4")


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

class Preflight(Listen):
    def test_listen_preflight_before_render(self):
        with self.subTest("the gateway off"):
            with mock.patch.dict(os.environ, {"YT_GATEWAY": "off"}):
                self.refused("YT_GATEWAY=vercel")
        with self.subTest("the engine not ready"):
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": ""}):
                self.refused("AI_GATEWAY_API_KEY")
        for model in ("anthropic/claude-opus-5", "openai/gpt-5"):
            with self.subTest("a model outside the allowlist", model=model):
                self.refused(model, "LISTEN_MODELS", listen_model=model)
        with self.subTest("mimo, its providers not yet verified"):
            self.refused("xiaomi/mimo-v2.6-flash", "no provider",
                         listen_model="xiaomi/mimo-v2.6-flash")
        with self.subTest("the author's family"):
            self.make_project(script={"engine": {"model": "google/gemini-3.6-pro"}})
            self.refused("script", "google/gemini-3.6-pro")
            self.make_project()
        with self.subTest("no film"):
            (self.root / "video.mp4").unlink()
            self.refused("video.mp4", "yt render")
            self.make_project()
        with self.subTest("a film older than its scene"):
            early = (self.root / "scene.xml").stat().st_mtime - 60
            os.utime(self.root / "video.mp4", (early, early))
            self.refused("older than scene.xml")
            self.make_project()
        with self.subTest("a silent build"):
            self.make_project(audio_tracks=0)
            self.refused("no audio track")
            self.make_project()
        with self.subTest("no ffprobe"):
            (self.bin / "ffprobe").rename(self.bin / "ffprobe.off")
            with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
                self.refused("ffprobe")
            (self.bin / "ffprobe.off").rename(self.bin / "ffprobe")
        with self.subTest("too large to send"):
            with mock.patch.object(_listen(), "LISTEN_MAX_MB", 0.0001):
                self.refused("MB", "LISTEN_MAX_MB")
        with self.subTest("a model with no price"):
            with mock.patch.dict(models.LISTEN_MODELS,
                                 {"xiaomi/mimo-v2.6-flash": {"providers": ("novita",)}}):
                self.refused("no price", listen_model="xiaomi/mimo-v2.6-flash")
        with self.subTest("an estimate over the budget"):
            self.refused("--listen-budget-usd", listen_budget_usd=0.000001)
        with self.subTest("--reuse of a pass drawn from another scene"):
            old = self.root / "qa" / "pass-01"
            old.mkdir(parents=True)
            (old / "samples.json").write_text("[]")
            (old / "scorecard.json").write_text(json.dumps({"scene_sha256": "another"}))
            self.refused("pass-01", "scene", reuse="pass-01")
            shutil.rmtree(self.root / "qa")
        # and with every piece in place it listens
        self.run_qa()
        self.assertTrue((self.root / "qa" / "pass-01" / "listening.json").is_file())

    def test_listen_own_family_refused(self):
        listen = _listen()
        # the script's model, through the gateway's model_family/maker rule
        self.make_project(script={"engine": {"model": "google/gemini-3.6-pro"}})
        self.refused("script", "google/gemini-3.6-pro")
        # an author named only among sound.json's answering models
        snd = listening._sound()
        snd["engine"] = {"model": "sonnet", "by_model": {"google/gemini-3.6-pro": {}}}
        self.make_project(sound=snd)
        self.refused("sound", "google/gemini-3.6-pro")
        # sound.json's usage has no model; its engine names the author
        snd = listening._sound()
        snd["engine"] = {"model": "google/gemini-3.6-pro"}
        snd["usage"] = {"cost_usd": 0.1, "calls": 1}
        self.make_project(sound=snd)
        self.refused("sound")
        # a voice engine that names a gateway model is an author too
        vo = listening._voice()
        vo["engine"] = "google/gemini-3.6-flash-tts"
        self.assertIn(("voice", "google/gemini-3.6-flash-tts"),
                      listen.authors({}, {}, vo))
        # a TTS engine's own name is not a model id
        self.assertEqual(listen.authors({}, {}, listening._voice()), [])
        # a claude author with the same listener listens
        self.make_project()
        self.run_qa()
        self.assertTrue(self.listening_json()["current"])

    def test_listen_family_refusal_names_the_listen_flags(self):
        # `yt qa --listen` has no --model or --reviewer-model: the refusal
        # names the stage file and --listen-model, which the operator can act on
        self.make_project(script={"engine": {"model": "google/gemini-3.6-pro"}})
        self.refused("script.json", "google/gemini-3.6-pro", "--listen-model", "google")
        self.assertNotIn("--reviewer-model", self.stderr)
        self.assertNotIn("--model ", self.stderr)
        # an author nobody can place is still refused, and says what settles it
        snd = listening._sound()
        snd["engine"] = {"model": "mystery-model"}
        self.make_project(sound=snd)
        self.refused("sound.json", "nobody can say who made mystery-model", "--listen-model")
        self.assertNotIn("--reviewer-model", self.stderr)


# --------------------------------------------------------------------------- #
# the audio
# --------------------------------------------------------------------------- #

class Audio(Listen):
    def test_listen_extracts_by_stream_copy(self):
        self.run_qa()
        calls = self.ffmpeg_calls()
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        joined = " ".join(argv)
        self.assertIn("-vn -map 0:a:0 -c copy -f adts", joined)
        self.assertIn(str(self.root / "video.mp4"), argv)
        for option in ("-c:a", "-acodec", "-b:a", "-ar", "-ac", "-q:a", "-filter:a", "-af"):
            self.assertNotIn(option, argv)
        out = Path(argv[-1])
        self.assertEqual(out.parent, self.root / "qa" / "listen")
        film = hashlib.sha256(b"film-1").hexdigest()
        self.assertTrue((self.root / "qa" / "listen" / f"{film[:16]}.aac").is_file())
        # the same film again: the extracted track is reused, ffmpeg is not run
        self.run_qa()
        self.assertEqual(len(self.ffmpeg_calls()), 1)
        record = self.listening_json("pass-02")
        audio = (self.bin / "audio.bin").read_bytes()
        self.assertEqual(record["audio"]["sha256"], hashlib.sha256(audio).hexdigest())
        self.assertEqual(record["audio"]["bytes"], len(audio))
        self.assertEqual(record["audio"]["media_type"], "audio/aac")
        self.assertEqual(record["film"]["sha256"], film)

    def test_listen_duration_from_ffprobe(self):
        (self.bin / "duration.txt").write_text("12.0\n")
        late = dict(FINDING, timestamp=13.0, problem="a click after the end")
        self.node.replies(ok(answer([FINDING, late])))
        self.run_qa()
        probe = self.ffprobe_calls()[-1]
        self.assertIn(str(self.root / "video.mp4"), probe)
        self.assertIn("a:0", probe)
        self.assertIn("stream=duration", probe)
        self.assertEqual(self.listening_json()["audio"]["seconds"], 12.0)
        problems = [f["problem"] for f in self.card()["findings"]]
        self.assertIn(FINDING["problem"], problems)
        self.assertNotIn("a click after the end", problems)
        with self.subTest("a duration that is not a number"):
            (self.bin / "duration.txt").write_text("N/A\n")
            self.refused("duration")

    def test_listen_sends_media_and_caches(self):
        self.node.replies(ok(answer(), routing={"resolvedProvider": "google"}))
        listener = _listen()._Listener
        built, engine = [], listener._engine

        def spy(harness, **config):
            made = engine(harness, **config)
            built.append(made.config)
            return made
        with mock.patch.object(listener, "_engine", spy):
            self.run_qa(listen_budget_usd=0.37)
        self.assertEqual(self.node.count, 1)
        # AUTO-124's runtime cap is the operator's budget, and its media cap
        # is the number the preflight measured the track against
        self.assertEqual([c.max_turns_budget_usd for c in built], [0.37])
        self.assertEqual([c.max_media_mb for c in built], [_listen().LISTEN_MAX_MB])
        self.assertEqual(self.listening_json()["served_model"], MODEL)
        self.assertEqual(self.listening_json()["provider"], "google")
        media = self.node.requests()[0]["media"]
        self.assertEqual(len(media), 1)
        self.assertTrue(media[0]["path"].endswith(".aac"))
        self.assertEqual(media[0]["mediaType"], "audio/aac")
        self.assertEqual(self.node.requests()[0]["model"], MODEL)
        self.assertEqual(self.node.requests()[0]["gateway"].get("only"), ["google"])
        name = self.listening_json()["decisions"][0]["name"]
        self.assertTrue((self.root / "decisions" / "qa" / f"{name}.json").is_file())
        # the same audio again: served from decisions/qa/, nothing asked or paid
        self.run_qa()
        self.assertEqual(self.node.count, 1)
        second = self.card("pass-02")
        self.assertTrue(all(d["cached"] for d in second["listen"]["engine"]["decisions"]))
        self.assertEqual(second["listen"]["engine"]["cost_usd"], 0)
        self.assertTrue(self.listening_json("pass-02")["current"])
        # served from the cache, it still says who answered: the pass that asked
        self.assertEqual(self.listening_json("pass-02")["served_model"], MODEL)
        self.assertEqual(self.listening_json("pass-02")["provider"], "google")
        self.assertEqual(second["listen"]["served_model"], MODEL)
        # a changed mix: a new film, a new track, asked again
        (self.root / "video.mp4").write_bytes(b"film-2")
        later = (self.root / "scene.xml").stat().st_mtime + 120
        os.utime(self.root / "video.mp4", (later, later))
        (self.bin / "audio.bin").write_bytes(b"\xff\xf1" + b"aac" * 299 + b"x")
        self.run_qa()
        self.assertEqual(self.node.count, 2)
        self.assertNotEqual(self.listening_json("pass-03")["audio"]["sha256"],
                            self.listening_json("pass-01")["audio"]["sha256"])


# --------------------------------------------------------------------------- #
# the answer, checked past the schema
# --------------------------------------------------------------------------- #

class Checked(Listen):
    def findings_section(self, md: str) -> str:
        start = md.index("## Findings")
        end = md.find("\n## ", start + 1)
        return md[start:end if end > 0 else None]

    def test_listen_timestamp_outside_film_asked_again_then_dropped(self):
        outside = dict(FINDING, timestamp=99.0, problem="a pop far past the end")
        self.node.replies(ok(answer([outside])))
        self.run_qa()
        self.assertEqual(self.node.count, 2)               # one more decide, no more
        again = self.node.requests()[1]["prompt"]
        self.assertIn("99", again)
        self.assertIn("outside", again)
        card = self.card()
        self.assertNotIn(outside["problem"], [f["problem"] for f in card["findings"]])
        self.assertTrue(any(outside["problem"] in n for n in card["not_evidenced"]),
                        card["not_evidenced"])
        dropped = self.listening_json()["dropped"]
        self.assertEqual([d["finding"]["problem"] for d in dropped], [outside["problem"]])

    def test_listen_again_keeps_the_first_answers_good_findings(self):
        good = dict(FINDING, timestamp=8.0, problem="the bed pumps under the voice",
                    owner="build", severity="minor")
        outside = dict(FINDING, timestamp=99.0, problem="a pop far past the end",
                       severity="minor")
        # the second answer files only the corrected finding, leaving out the
        # two the check kept, and repeats one of them besides
        fixed = dict(outside, timestamp=15.0)
        self.node.replies(ok(answer([FINDING, good, outside])),
                          ok(answer([fixed, dict(good, timestamp=8.2)])))
        self.run_qa()
        self.assertEqual(self.node.count, 2)
        again = self.node.requests()[1]["prompt"]
        self.assertIn("only", again)
        record = self.listening_json()
        heard = sorted((f["timestamp"], f["problem"]) for f in record["findings"])
        self.assertEqual(heard, [(3.2, FINDING["problem"]), (8.0, good["problem"]),
                                 (15.0, outside["problem"])])
        self.assertEqual(record["dropped"], [])
        self.assertEqual(record["result"]["categories"]["sound_design"]["findings"][0],
                         FINDING)
        self.assertEqual(record["again"]["categories"]["sound_design"]["findings"][0], fixed)
        card = self.card()
        self.assertFalse(any("a pop far past the end" in n for n in card["not_evidenced"]))

    def test_listen_prompt_contents(self):
        listen = _listen()
        script = {"sections": [{"shots": [{"label": "1.1"}, {"label": "1.2"}]}]}
        prompt = listen.listen_prompt("One take", SECONDS, script=script,
                                      build=motion._build(audio_tracks=3),
                                      voice=listening._voice(), sound=listening._sound())
        # the narration as lines of text, not one escaped JSON string
        self.assertIn("This comet is leaving the Sun.", prompt)
        self.assertIn("And its tail is in front.", prompt)
        self.assertNotIn("\\n", prompt)
        self.assertIn("The narration as written", prompt)
        self.assertIn("context and not evidence", prompt)
        self.assertIn("The cue sheet as spotted: context, not evidence", prompt)
        self.assertIn("seconds from the start of this audio", prompt)
        self.assertIn(f"0 to {SECONDS:g}", prompt)
        # the cue sheet's place, length, kind and level, nothing else of it
        for needle in ('"at": 3', '"seconds": 1', '"kind": "sfx"', '"level": "-12 dBFS peak"'):
            self.assertIn(needle, prompt)
        self.assertNotIn("sound/s1.mp3", prompt)
        # and what the gateway was asked is that prompt
        self.run_qa()
        sent = self.node.requests()[0]["prompt"]
        self.assertIn("The cue sheet as spotted: context, not evidence", sent)
        self.assertIn('"kind": "sfx"', sent)

    def test_listen_enum_violation_dropped(self):
        bad = dict(FINDING, owner="editor", severity="major", problem="the bed pumps")
        self.node.replies(ok(answer([bad])))
        self.run_qa()
        # decide's own ask-again, then the listen's one more ask, and its own again
        self.assertEqual(self.node.count, 4)
        card = self.card()
        self.assertEqual([f for f in card["findings"] if f.get("source") == "listen"], [])
        self.assertTrue(any("the bed pumps" in n and "editor" in n
                            for n in card["not_evidenced"]), card["not_evidenced"])
        md = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertNotIn("the bed pumps", self.findings_section(md))

    def test_listen_findings_merged_and_block(self):
        self.run_qa()
        card = self.card()
        heard = [f for f in card["findings"] if f.get("source") == "listen"]
        self.assertEqual(len(heard), 1)
        self.assertEqual(heard[0]["frame"], "listening.json")
        self.assertEqual(heard[0]["owner"], "sound")
        self.assertEqual(heard[0]["category"], "sound_design")
        self.assertIn(f"{FINDING['problem']} at {FINDING['timestamp']}s", card["blocking"])
        self.assertEqual(card["verdict"], qa.NOT_READY)
        md = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        section = self.findings_section(md)
        self.assertIn(FINDING["problem"], section)
        self.assertIn("frame: listening.json", section)
        self.assertIn("owner: **sound**", section)
        # what the listener could not hear is named, never scored
        self.assertTrue(any("in sync with the lips" in n for n in card["not_evidenced"]))

    def test_listen_role_and_brief(self):
        self.run_qa()
        self.assertTrue(Recording.systems)
        for system in Recording.systems:
            self.assertIn(qa.ROLE_LISTENED, system)
            self.assertNotIn(qa.ROLE, system)
        self.assertIn("listening.json", qa.ROLE_LISTENED)
        prompt = self.prompt()
        self.assertIn(f"Listening review: {MODEL} heard the rendered audio of this cut", prompt)
        self.assertEqual(Recording.stream_calls[-1]["cwd"], self.root / "qa" / "pass-01")
        self.assertTrue((self.root / "qa" / "pass-01" / "listening.json").is_file())
        card = self.card()
        row = next(r for r in card["listening"] if r["source"] == "listen")
        self.assertTrue(row["current"])
        self.assertEqual(sorted(row["covers"]), sorted(qa.AUDIO))
        self.assertEqual(card["audio_not_evidenced"], {})

    def test_with_no_other_review_the_listen_alone_evidences_the_audio(self):
        snd, vo = listening._sound(), listening._voice()
        del snd["review"], vo["review"]
        self.make_project(sound=snd, voice=vo)
        self.run_qa()
        card = self.card()
        self.assertEqual(card["audio_not_evidenced"], {})
        for key in qa.AUDIO:
            self.assertIsNotNone(card["scores"][key])


# --------------------------------------------------------------------------- #
# reuse, failure, spend
# --------------------------------------------------------------------------- #

class Records(Listen):
    def test_listen_reuse(self):
        self.run_qa(listen=False)
        self.assertEqual(self.node.count, 0)
        self.run_qa(reuse="pass-01")
        self.assertEqual(self.passes(), ["pass-01"])
        record = self.listening_json()
        self.assertTrue(record["current"])
        self.assertEqual(record["film"]["sha256"],
                         hashlib.sha256((self.root / "video.mp4").read_bytes()).hexdigest())
        # a pass whose frames show another scene is refused
        card = self.card()
        card["scene_sha256"] = "another"
        (self.root / "qa" / "pass-01" / "scorecard.json").write_text(json.dumps(card))
        count = self.node.count
        code = self.run_qa(reuse="pass-01")
        self.assertEqual(code, 1)
        self.assertIn("scene", self.stderr)
        self.assertEqual(self.node.count, count)

    def assert_failed_listen(self, why):
        code = self.run_qa()
        card = self.card()                       # the scorecard is still written
        self.assertIn(code, (0, 1))
        listen = card["listen"]
        self.assertFalse(listen["current"])
        self.assertIn(why, listen["reason"])
        rows = listen["engine"]["decisions"]
        self.assertEqual([r["kind"] for r in rows], ["failed"])
        self.assertGreater(rows[0]["cost_usd"], 0)
        self.assertAlmostEqual(card["usage"]["cost_usd"],
                               round(card["engine"]["cost_usd"] + rows[0]["cost_usd"], 4))
        external = [f for f in card["findings"] if f.get("owner") == "external"
                    and f.get("source") == "listen"]
        self.assertEqual(len(external), 1)
        self.assertIn(why, external[0]["fix"] + external[0]["root_cause"])
        self.assertFalse(self.listening_json()["current"])
        row = next(r for r in card["listening"] if r["source"] == "listen")
        self.assertFalse(row["current"])
        # the frame review still ran, under today's role
        self.assertTrue(Recording.stream_calls)
        self.assertIn(qa.ROLE, Recording.systems[-1])

    def test_listen_failure_recorded(self):
        with self.subTest("a 402"):
            self.node.replies(err("gateway", 3, usage=USAGE, status=402, retryable=False,
                                  message="Insufficient funds: quota_for_entity_exceeded"))
            self.assert_failed_listen("402")
            self.assertEqual(self.node.count, 1)          # never retried
        with self.subTest("another model answered"):
            self.make_project()
            self.node.replies(ok(answer(), model="google/gemini-3.6-pro"))
            self.assert_failed_listen("google/gemini-3.6-pro")

    def test_a_film_rendered_during_the_listen_is_not_current(self):
        listener = _listen()._Listener
        decide = listener.decide

        def then_render(harness, *a, **kw):
            out = decide(harness, *a, **kw)
            (self.root / "video.mp4").write_bytes(b"film-rendered-meanwhile")
            return out
        with mock.patch.object(listener, "decide", then_render):
            self.run_qa()
        record = self.listening_json()
        self.assertFalse(record["current"])
        self.assertIn("changed while the listener heard it", record["reason"])
        self.assertEqual([f["owner"] for f in record["findings"]], ["external"])
        row = next(r for r in self.card()["listening"] if r["source"] == "listen")
        self.assertFalse(row["current"])
        self.assertIn(qa.ROLE, Recording.systems[-1])

    def test_a_scene_edited_during_the_listen_is_not_current(self):
        listener = _listen()._Listener
        decide = listener.decide

        def then_edit(harness, *a, **kw):
            out = decide(harness, *a, **kw)
            later = (self.root / "video.mp4").stat().st_mtime + 60
            os.utime(self.root / "scene.xml", (later, later))
            return out
        with mock.patch.object(listener, "decide", then_edit):
            self.run_qa()
        record = self.listening_json()
        self.assertFalse(record["current"])
        self.assertIn("older than scene.xml", record["reason"])
        self.assertEqual([f["owner"] for f in record["findings"]], ["external"])
        row = next(r for r in self.card()["listening"] if r["source"] == "listen")
        self.assertFalse(row["current"])

    def test_pass_usd_leaves_the_listen_out(self):
        # one scoring sample asked, the reviewer's 0.30 and the listen's 0.10:
        # the next pass is QA_SAMPLES samples of the reviewer's, and a listen
        # only when --listen is asked again (spend.FLAG_USD prices it then)
        card = {"usage": spend.record(0.40, "sonnet", 2),
                "engine": {"cost_usd": 0.30, "decisions": []},
                "scoring": {"asked": 1},
                "listen": {"engine": {"cost_usd": 0.10, "decisions": [
                    {"name": "listen-0123", "cached": False, "cost_usd": 0.10}]}}}
        self.assertEqual(qa.pass_usd(card), round(0.30 * qa.QA_SAMPLES, 2))
        # a failed listen is billed on the card, and left out the same way
        card["listen"]["engine"]["decisions"][0]["kind"] = "failed"
        self.assertEqual(qa.pass_usd(card), round(0.30 * qa.QA_SAMPLES, 2))

    def test_listen_usage_merged(self):
        self.run_qa()
        card = self.card()
        by_model = card["usage"]["by_model"]
        self.assertIn("claude-sonnet-5-5", by_model)
        self.assertIn(MODEL, by_model)
        self.assertEqual(card["usage"]["model"], "sonnet")
        listened = card["listen"]["engine"]["cost_usd"]
        self.assertGreater(listened, 0)
        self.assertAlmostEqual(card["usage"]["cost_usd"],
                               round(card["engine"]["cost_usd"] + listened, 4))
        self.assertNotIn(MODEL, card["engine"]["by_model"])     # the reviewer's alone
        self.assertEqual(card["engine"]["model"], "sonnet")
        record = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(record["usage"], card["usage"])
        self.assertAlmostEqual(spend._qa_cost(self.root), card["usage"]["cost_usd"])
        # the next pass's price is the reviewer's alone: a plain `yt qa` does
        # not listen, and --listen adds its own estimate (spend.FLAG_USD)
        self.assertAlmostEqual(qa.pass_usd(card), round(card["engine"]["cost_usd"], 2),
                               places=2)

    def test_listen_flag_increment(self):
        self.run_qa(listen=False)
        added = dict(spend.flag_increments(self.root, "qa", ["--listen"]))
        self.assertGreater(added.get("--listen", 0), 0)
        row = next(r for r in spend.stage_estimates(self.root, [], extras={"qa": ["--listen"]})
                   if r["stage"] == "qa")
        self.assertIn("--listen", row["source"])
        plain = next(r for r in spend.stage_estimates(self.root, []) if r["stage"] == "qa")
        # A last pass that listened does not hold the next listen's cost: its
        # price leaves the listen out (qa.pass_usd), so --listen adds it again,
        # whether that listen was paid, served from the cache or failed.
        self.run_qa()
        self.assertGreater(dict(spend.flag_increments(self.root, "qa", ["--listen"]))
                           .get("--listen", 0), 0)
        again = next(r for r in spend.stage_estimates(self.root, []) if r["stage"] == "qa")
        self.assertEqual(again["usd"], plain["usd"])
        self.assertNotIn("--listen", again["source"])
        self.run_qa()                                          # the same mix: cached
        self.assertTrue(self.card("pass-03")["listen"]["engine"]["decisions"][0]["cached"])
        self.assertGreater(dict(spend.flag_increments(self.root, "qa", ["--listen"]))
                           .get("--listen", 0), 0)
        self.make_project(film=b"film-3")
        self.node.replies(err("gateway", 3, usage=USAGE, status=402, retryable=False,
                              message="Insufficient funds: quota_for_entity_exceeded"))
        self.run_qa()
        self.assertEqual(self.card("pass-01")["listen"]["engine"]["decisions"][0]["kind"],
                         "failed")
        self.assertGreater(dict(spend.flag_increments(self.root, "qa", ["--listen"]))
                           .get("--listen", 0), 0)
        # with no record at all the flag is priced from its defaults
        self.assertGreater(dict(spend.flag_increments(None, "qa", ["--listen"]))["--listen"], 0)


if __name__ == "__main__":
    unittest.main()
