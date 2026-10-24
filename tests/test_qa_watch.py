"""yt qa --watch: a model that watches the rendered cut through the gateway (AUTO-127).

QA judges stills: a still sampler misses what happens between samples (a
two-frame pop, a stuttering transition, a super that lands late). No
Anthropic or OpenAI model takes video, so `--watch` sends the cut, cut down
on a render host when it is too large, to a gateway model from another
maker than every author of the film, and turns what it saw into extra
sampled moments that the scorecard's reviewer confirms against its stills.

The claude and codex CLIs stay the default. Without --watch nothing here
runs: no catalog fetch, no node, no ssh, and the reviewer's prompt, schema
and samples are what they were.

Nothing reaches a model, the network or a render host: the gateway engine
runs a fake `node` (tests/gateway_fakes.py), the catalog is a fixture
(tests/fixtures/gateway/), ssh and rsync are a fake subprocess.run under
hermetic.fake_remote, and the scorecard reviewer is a fake engine.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import importlib  # noqa: E402

import gateway_engine  # noqa: E402
import hermetic  # noqa: E402
from claude_engine import Event  # noqa: E402
from gateway_fakes import FakeNode, err, ok  # noqa: E402
from tests import test_qa_listen as listen_tests  # noqa: E402
from tests import test_qa_listening as listening  # noqa: E402
from tests import test_qa_motion as motion  # noqa: E402
from ytstudio import cli, doctor, harness, qa, render, spend, workspace  # noqa: E402

CATALOG = json.loads((ROOT / "tests" / "fixtures" / "gateway"
                      / "models-2026-10-23.excerpt.json").read_text())


def _watch():
    """The watch module, imported when a test needs it (it is new)."""
    return importlib.import_module("ytstudio.watch")


def _row(model: str) -> dict:
    return dict(next(r for r in CATALOG["data"] if r["id"] == model))


def _stale_partial(tmp: Path) -> workspace.Project:
    """A project whose final render covers 0-10 s of a 16.5 s programme and
    whose video.mp4 is older than scene.xml."""
    root = tmp / "cut"
    root.mkdir()
    (root / "scene.xml").write_text('<project duration="16.5"/>')
    (root / "build.json").write_text(json.dumps({"scene": {"duration": 16.5},
                                                 "audio_tracks": 2}))
    (root / "render.json").write_text(json.dumps({"ok": True, "result": {
        "outputs": [{"quality": "final", "range": [0, 10]}]}}))
    (root / "video.mp4").write_bytes(b"x")
    early = (root / "scene.xml").stat().st_mtime - 60
    os.utime(root / "video.mp4", (early, early))
    return workspace.Project(root)


class CutProblems(unittest.TestCase):
    """doctor's two rules about the cut, one function both doctor and qa call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-watch-cut-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_doctor_cut_problems_unchanged(self):
        project = _stale_partial(self.tmp)
        final = f"yt render --project {project.root} --quality final --strict"
        # what delivery_evidence said before cut_problems existed, word for word
        self.assertEqual(doctor.delivery_evidence(project, None), [
            ("the render covers 0-10s of a 16.5s programme", final),
            ("video.mp4 predates scene.xml: the scene was rebuilt after the render, so "
             "the video shows an older cut", final),
            ("no delivery gate has run on this render", f"yt gate --project {project.root}"),
            ("no QA pass has scored this render", f"yt qa --project {project.root}"),
            # and the upload set's row since AUTO-147 (doctor._package_rows)
            ("no upload set: package/package.json has not been written for this render",
             f"yt package --title {project.root.name}")])
        self.assertEqual(doctor.cut_problems(project, None), [
            ("the render covers 0-10s of a 16.5s programme", final),
            ("video.mp4 predates scene.xml: the scene was rebuilt after the render, so "
             "the video shows an older cut", final)])

    def test_a_current_whole_cut_has_no_problem(self):
        project = _stale_partial(self.tmp)
        (project.root / "render.json").write_text(json.dumps({"ok": True, "result": {
            "outputs": [{"quality": "final", "range": [0, 16.5]}]}}))
        later = (project.root / "scene.xml").stat().st_mtime + 60
        os.utime(project.root / "video.mp4", (later, later))
        self.assertEqual(doctor.cut_problems(project, None), [])


# --------------------------------------------------------------------------- #
# the model gate
# --------------------------------------------------------------------------- #

class Gate(unittest.TestCase):
    """watch.model_error(model, catalog, authors): "" when the model may watch."""

    def error(self, model, catalog=None, authors=()):
        return _watch().model_error(model, CATALOG["data"] if catalog is None else catalog,
                                    list(authors))

    def test_the_default_watcher_may_watch(self):
        self.assertEqual(_watch().WATCH_MODEL, "google/gemini-3.6-flash")
        self.assertEqual(self.error("google/gemini-3.6-flash", authors=["sonnet"]), "")

    def test_watch_refuses_model_without_video_input(self):
        for model in ("anthropic/claude-opus-5", "google/gemini-3.1-pro-preview"):
            with self.subTest(model=model):
                said = self.error(model)
                self.assertIn(model, said)
                self.assertIn("video-input", said)

    def test_watch_refuses_generation_and_no_so(self):
        said = self.error("alibaba/wan-v3.0-video")
        self.assertIn("video", said)
        self.assertIn("language", said)
        said = self.error("elorian/glimpse")
        self.assertIn("structured-output", said)

    def test_watch_refuses_deprecated_vmc_and_batch(self):
        old = dict(_row("google/gemini-3.7-flash"), deprecated_at="2026-11-01T00:00:00Z")
        self.assertIn("deprecated", self.error("google/gemini-3.7-flash", catalog=[old]))
        virtual = dict(_row("google/gemini-3.6-flash"), id="vmc/watcher")
        self.assertIn("vmc/", self.error("vmc/watcher", catalog=[virtual]))
        self.assertIn("-batch", self.error("google/gemini-3.6-flash-batch"))

    def test_a_model_the_catalog_lacks_is_refused(self):
        self.assertIn("not in the gateway's catalog", self.error("google/gemini-9-flash"))

    def test_watch_refuses_same_maker_as_author(self):
        # vfx.json's by_model holds another Google model: one maker
        said = self.error("google/gemini-3.6-flash",
                          authors=["sonnet", "google/gemini-3.7-flash"])
        self.assertIn("google/gemini-3.7-flash", said)
        self.assertIn("google", said)
        self.assertEqual(self.error("zai/glm-5.3-flash",
                                    authors=["sonnet", "google/gemini-3.7-flash"]), "")

    def test_watch_refuses_unknown_author_maker(self):
        said = self.error("google/gemini-3.6-flash", authors=["sonnet", "mystery-model-7"])
        self.assertIn("mystery-model-7", said)
        self.assertIn("maker", said)


class Authors(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-watch-authors-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def put(self, name, data):
        (self.tmp / name).write_text(json.dumps(data))

    def test_author_models_reads_every_stage(self):
        self.put("script.json", {"engine": {"model": "sonnet",
                                            "by_model": {"claude-sonnet-5-5": {}}}})
        self.put("keyframe.json", {"engine": {
            "elements": {"model": "opus", "by_model": {"claude-opus-5-5": {}}},
            "review": {"model": "gpt-5.5", "by_model": {"gpt-5.5": {}}}}})
        self.put("vfx.json", {"engine": {"model": "sonnet", "by_model": {}},
                              "authored": [
                                  {"n": 1, "engine": {"model": "opus",
                                                      "by_model": {"google/gemini-3.7-flash": {}}}},
                                  {"n": 2, "engine": {"model": "zai/glm-5.3", "by_model": {}}}]})
        self.put("sound.json", {"engine": {"model": "haiku",
                                           "by_model": {"claude-haiku-4-5": {}}}})
        found = _watch().author_models(self.tmp)
        models = {m for _, m in found}
        self.assertEqual(models, {"sonnet", "claude-sonnet-5-5", "opus", "claude-opus-5-5",
                                  "gpt-5.5", "google/gemini-3.7-flash", "zai/glm-5.3",
                                  "haiku", "claude-haiku-4-5"})
        self.assertIn(("keyframe.json", "gpt-5.5"), found)
        self.assertIn(("vfx.json", "google/gemini-3.7-flash"), found)

    def test_decisions_models_are_not_read(self):
        # a decision row's model is the one asked for (Harness._note), not
        # who answered; by_model is who answered
        self.put("script.json", {"engine": {"model": "sonnet", "by_model": {},
                                            "decisions": [{"name": "x", "model": "mystery"}]}})
        self.assertEqual([m for _, m in _watch().author_models(self.tmp)], ["sonnet"])

    def test_no_records_no_authors(self):
        self.assertEqual(_watch().author_models(self.tmp), [])


# --------------------------------------------------------------------------- #
# the plan: what is sent, fitted under the limit
# --------------------------------------------------------------------------- #

def _shots(lengths) -> list[dict]:
    out, t = [], 0.0
    for n, seconds in enumerate(lengths, 1):
        out.append({"label": f"s{n}", "at": round(t, 3), "seconds": seconds})
        t += seconds
    return out


class Plan(unittest.TestCase):
    MB = 1024 * 1024

    def plan(self, duration, size, shots, **kw):
        kw.setdefault("max_bytes", 14 * self.MB)
        kw.setdefault("max_seconds", 600.0)
        kw.setdefault("kbps", 600)
        return _watch().watch_plan(duration, size, shots, **kw)

    def test_watch_plan_as_is(self):
        segs = self.plan(45.0, 9 * self.MB, _shots([15, 15, 15]))
        self.assertEqual(_watch().plan_kind(segs), "as_is")
        self.assertEqual([(s.start, s.end, s.scale) for s in segs], [(0.0, 45.0, None)])

    def test_watch_plan_downscale(self):
        # a 1080p master too large to send, short enough to fit when downscaled
        segs = self.plan(45.0, 60 * self.MB, _shots([15, 15, 15]))
        self.assertEqual(_watch().plan_kind(segs), "downscale")
        self.assertEqual([(s.start, s.end, s.scale) for s in segs],
                         [(0.0, 45.0, _watch().WATCH_HEIGHT)])
        self.assertLessEqual(_watch().predicted_bytes(45.0, 600), 14 * self.MB)

    def test_watch_plan_downscale_split(self):
        lengths = [10.0] * 81 + [5.0]            # 13:35, 815 s, 82 shots
        shots = _shots(lengths)
        duration = sum(lengths)
        segs = self.plan(duration, 1700 * self.MB, shots)
        self.assertEqual(_watch().plan_kind(segs), "downscale_split")
        cap = min(600.0, 0.9 * 14 * self.MB / (600 * 125))
        bounds = {0.0, duration} | {s["at"] for s in shots}
        self.assertEqual(segs[0].start, 0.0)
        self.assertEqual(segs[-1].end, duration)
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a.end, b.start, "segments are contiguous between shots")
        for s in segs:
            self.assertIn(s.start, bounds)
            self.assertIn(s.end, bounds)
            self.assertLessEqual(s.end - s.start, cap + 1e-9)
            self.assertLessEqual(_watch().predicted_bytes(s.end - s.start, 600), 14 * self.MB)
            self.assertEqual(s.scale, _watch().WATCH_HEIGHT)

    def test_a_shot_longer_than_the_cap_is_cut_inside_with_one_second_of_overlap(self):
        cap = 0.9 * 14 * self.MB / (600 * 125)        # about 176 s
        shots = _shots([20.0, 400.0, 20.0])            # the middle shot is over twice the cap
        duration = 440.0
        segs = self.plan(duration, 600 * self.MB, shots)
        self.assertEqual(_watch().plan_kind(segs), "downscale_split")
        self.assertEqual(segs[0].start, 0.0)
        self.assertEqual(segs[-1].end, duration)
        overlaps = [round(a.end - b.start, 6) for a, b in zip(segs, segs[1:])]
        inside = [a.end for a in segs[:-1] if 20.0 < a.end < 420.0]
        self.assertTrue(inside, "the long shot is cut inside itself")
        for a, b in zip(segs, segs[1:]):
            if 20.0 < a.end < 420.0:
                self.assertEqual(round(a.end - b.start, 6), 1.0)   # inside: 1 s of overlap
            else:
                self.assertEqual(round(a.end - b.start, 6), 0.0)   # at a boundary: none
        self.assertIn(1.0, overlaps)
        for s in segs:
            self.assertLessEqual(s.end - s.start, cap + 1e-9)

    def test_too_long_to_send_whole_is_split_even_when_small(self):
        segs = self.plan(900.0, 5 * self.MB, _shots([300.0, 300.0, 300.0]))
        self.assertEqual(_watch().plan_kind(segs), "downscale_split")
        self.assertTrue(all(s.end - s.start <= 600.0 for s in segs))


# --------------------------------------------------------------------------- #
# preparing the cut on a render host
# --------------------------------------------------------------------------- #

class FakeHost:
    """ssh and rsync, faked: the argv of each launch, an ssh that answers
    `@@OUT` lines, an rsync back that writes the segments it names. Any
    other launch (the fake `node`) runs as it would."""

    def __init__(self, case, *, sizes=None, seconds=None, ssh_stdout=None):
        self.calls: list[list[str]] = []
        self.removed: list[tuple[str, str]] = []
        self.sizes, self.seconds, self.ssh_stdout = sizes, seconds, ssh_stdout
        self.real = subprocess.run
        self.segments: list = []
        hermetic.fake_remote(case)
        for target, name, value in ((_watch().subprocess, "run", self.run),
                                    (render, "remove_remote", self.remove)):
            p = mock.patch.object(target, name, value)
            p.start()
            case.addCleanup(p.stop)
        p = mock.patch.dict(render._HOSTS, {"render-1": render.Host(
            addr="render-1", engine="/opt/sr/scene-render", ffprobe="/opt/af/bin/ffprobe")})
        p.start()
        case.addCleanup(p.stop)

    def remove(self, host, remote, **kw):
        self.removed.append((host, remote))

    def run(self, argv, *a, **kw):
        if not argv or argv[0] not in ("ssh", "rsync"):
            return self.real(argv, *a, **kw)
        self.calls.append(list(argv))
        out = ""
        if argv[0] == "ssh":
            n = argv[-1].count("@@OUT ")
            out = self.ssh_stdout if self.ssh_stdout is not None else "".join(
                f"@@OUT {k} {self.size(k)} {self.length(k)}\n" for k in range(1, n + 1))
        elif argv[-2].endswith("seg-*.mp4"):
            dest = Path(argv[-1])
            for k in range(1, len(self.segments) + 1):
                (dest / f"seg-{k:02d}.mp4").write_bytes(b"s" * min(self.size(k), 4096))
        return subprocess.CompletedProcess(argv, 0, out, "")

    def size(self, k):
        return (self.sizes or {}).get(k, 2048)

    def length(self, k):
        if self.seconds and k in self.seconds:
            return self.seconds[k]
        seg = self.segments[k - 1]
        return f"{seg.end - seg.start:.3f}"


class Prepare(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-watch-prep-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cut = self.tmp / "video.mp4"
        self.cut.write_bytes(b"master" * 100)
        self.segs = [_watch().Segment(0.0, 170.0, 480), _watch().Segment(170.0, 340.0, 480)]

    def prep(self, host):
        host.segments = self.segs
        return _watch().plan_remote(self.cut, "render-1", "one-take", self.segs,
                                    self.tmp / "watch")

    def test_prepare_runs_on_host_not_here(self):
        host = FakeHost(self)
        prep = self.prep(host)
        self.assertTrue(prep.root.startswith(render.REMOTE_ROOT + "/one-take/watch-"))
        rows = _watch().prepare_remote(prep)
        up, ssh, back = host.calls
        self.assertEqual(up[0], "rsync")
        self.assertEqual(up[-2:], [str(self.cut), f"render-1:{prep.root}/cut.mp4"])
        self.assertNotIn("--delete", up)
        self.assertEqual(ssh[0], "ssh")
        self.assertIn("render-1", ssh)
        self.assertIn(f"cd {prep.root}", ssh[-1])
        self.assertIn("command -v ffmpeg", ssh[-1])
        self.assertIn("/opt/af/bin/ffmpeg", ssh[-1])           # beside its ffprobe
        self.assertIn(" -an ", ssh[-1])
        self.assertIn("scale=-2:480", ssh[-1])
        self.assertEqual(back[:2], ["rsync", "-az"])
        self.assertEqual(back[-2:], [f"render-1:{prep.root}/seg-*.mp4",
                                     f"{self.tmp / 'watch'}/"])
        self.assertEqual(host.removed, [("render-1", prep.root)])
        self.assertEqual([r["n"] for r in rows], [1, 2])
        for argv in host.calls:
            self.assertNotIn(Path(argv[0]).name, ("ffmpeg", "ffprobe"))

    def test_a_scan_rides_the_upload_before_the_directory_goes(self):
        # AUTO-141: `yt qa`'s rhythm scan reads the cut the watch already
        # sent, so it goes up once; the host directory is still there then.
        host = FakeHost(self)
        prep = self.prep(host)
        seen = []
        _watch().prepare_remote(prep, while_up=lambda h, path, upload: seen.append(
            (h, path, [c[0] for c in host.calls], list(host.removed), upload)))
        self.assertEqual([s[:4] for s in seen],
                         [("render-1", f"{prep.root}/cut.mp4", ["rsync"], [])])
        # the one upload's size and time go with it, for rhythm.json
        upload = seen[0][4]
        self.assertEqual(upload["upload_bytes"], Path(prep.cut).stat().st_size)
        self.assertGreaterEqual(upload["upload_seconds"], 0.0)
        self.assertEqual(host.removed, [("render-1", prep.root)])

    def test_prepare_refused_under_no_remote(self):
        calls = []
        with mock.patch.object(_watch().subprocess, "run", lambda *a, **k: calls.append(a)), \
                mock.patch.object(render, "remove_remote", lambda *a, **k: calls.append(a)), \
                mock.patch.dict(os.environ, {render.NO_REMOTE_ENV: "1"}), \
                mock.patch.dict(render._HOSTS, {"render-1": render.Host(addr="render-1")}):
            prep = _watch().plan_remote(self.cut, "render-1", "one-take", self.segs,
                                        self.tmp / "watch")
            with self.assertRaises(_watch().WatchError) as caught:
                _watch().prepare_remote(prep)
        self.assertIn(render.NO_REMOTE_ENV, str(caught.exception))
        self.assertEqual(calls, [])

    def test_prepare_output_over_limit_not_sent(self):
        host = FakeHost(self, sizes={2: _watch().WATCH_MAX_BYTES + 1})
        prep = self.prep(host)
        with self.assertRaises(_watch().WatchError) as caught:
            _watch().prepare_remote(prep)
        self.assertIn("seg-02.mp4", str(caught.exception))
        self.assertIn("over WATCH_MAX_BYTES", str(caught.exception))
        self.assertEqual([c[0] for c in host.calls], ["rsync", "ssh"])   # nothing back
        self.assertEqual(host.removed, [("render-1", prep.root)])

    def test_a_segment_off_its_plan_or_a_host_without_ffmpeg_is_an_error(self):
        host = FakeHost(self, seconds={1: "160.0"})
        with self.assertRaises(_watch().WatchError) as caught:
            _watch().prepare_remote(self.prep(host))
        self.assertIn("lasts 160 s", str(caught.exception))
        host.ssh_stdout = "__NO_FFMPEG__\n"
        with self.assertRaises(_watch().WatchError) as caught:
            _watch().prepare_remote(self.prep(host))
        self.assertIn("no ffmpeg on render host render-1", str(caught.exception))

    def test_watch_media_cap_agrees_with_engine(self):
        node = FakeNode(self)
        exact, over = self.tmp / "exact.mp4", self.tmp / "over.mp4"
        for path, size in ((exact, _watch().WATCH_MAX_BYTES),
                           (over, _watch().WATCH_MAX_BYTES + 1)):
            with open(path, "wb") as f:
                f.truncate(size)
        engine = node.engine(max_media_mb=_watch().WATCH_MAX_BYTES / (1024 * 1024))
        self.assertEqual(len(engine._media([exact])), 1)
        with self.assertRaises(gateway_engine.GatewayExecutionError):
            engine._media([over])
        # and the host's report: exactly the limit passes, a byte more does not
        host = FakeHost(self, sizes={1: _watch().WATCH_MAX_BYTES, 2: 10})
        _watch().prepare_remote(self.prep(host))
        host.sizes = {1: _watch().WATCH_MAX_BYTES + 1, 2: 10}
        with self.assertRaises(_watch().WatchError):
            _watch().prepare_remote(self.prep(host))


# --------------------------------------------------------------------------- #
# the answer, checked and merged
# --------------------------------------------------------------------------- #

def _finding(start, end, category="animation_vfx", severity="minor", problem="a pop"):
    return {"start": start, "end": end, "category": category, "problem": problem,
            "why": "it jumps", "owner": "vfx", "fix": "ease it", "severity": severity}


def _answer(findings=(), seconds=16.5, unseen=("the sound",)):
    return {"watched_seconds": seconds, "findings": list(findings),
            "not_evidenced": list(unseen)}


class Answer(unittest.TestCase):
    def test_watch_times_mapped_and_merged(self):
        seg = _watch().Segment
        out = _watch().merge_segments([
            (seg(0.0, 31.0, 480), [_finding(30.4, 30.8, problem="a stutter")]),
            (seg(30.0, 60.0, 480), [_finding(4.0, 4.5),
                                    _finding(0.5, 0.9, problem="the same stutter"),
                                    _finding(0.6, 0.9, category="editing_pacing")])])
        self.assertEqual([(f["id"], f["start"], f["category"]) for f in out], [
            ("W1", 30.4, "animation_vfx"), ("W2", 30.6, "editing_pacing"),
            ("W3", 34.0, "animation_vfx")])
        self.assertEqual(out[0]["segments"], [1, 2])      # seen in the overlap, kept once

    def test_the_checks_past_the_schema(self):
        check = _watch().check
        self.assertEqual(check(_answer([_finding(1, 2)], seconds=16.0), 16.5)[1], [])
        kept, problems = check(_answer([_finding(1, 30)]), 16.5)
        self.assertEqual(kept, [])
        self.assertIn("not inside the video", problems[0])
        self.assertIn("watched_seconds is 0", check(_answer(seconds=0), 16.5)[1][0])
        self.assertEqual(check(_answer(seconds=15.0), 16.5)[1], [])    # within 2 s
        self.assertTrue(check(_answer(seconds=500.0), 600.0)[1])        # off by over 10 %

    def test_watched_majority_over_three_samples(self):
        record = {"model": "m", "findings": [dict(_finding(7.6, 8.0), id="W1")]}
        samples = [{"t": 7.6, "frame": "t_00m07s600.png", "watched": ["W1"],
                    "burst": {"files": ["burst/t_00m07s600_0228.png"]}}]
        frames = Path(tempfile.mkdtemp(prefix="qa-watch-frames-"))
        self.addCleanup(shutil.rmtree, frames, True)
        (frames / "t_00m07s600.png").write_bytes(b"png")

        def readings(*said, valid=3):
            return [{"k": k, "valid": k <= valid, "card": {"watched": [
                {"id": "W1", "verdict": v, "frame": "t_00m07s600.png", "why": v}]}}
                for k, v in enumerate(said, 1)]
        verdicts = _watch().verdicts
        yes, no, _ = verdicts(record, readings("confirmed", "confirmed", "disagree"),
                              samples, frames)
        self.assertEqual([f["id"] for f in yes], ["W1"])
        yes, no, _ = verdicts(record, readings("confirmed", "not_visible", "disagree"),
                              samples, frames)
        self.assertEqual(yes, [])
        self.assertEqual([v["verdict"] for v in no[0]["votes"]],
                         ["confirmed", "not_visible", "disagree"])
        yes, no, _ = verdicts(record, readings("confirmed", "confirmed", "confirmed", valid=1),
                              samples, frames)
        self.assertEqual(yes, [])                 # fewer than two valid samples
        # a confirmation naming a frame that is not its still counts as not_visible
        bad = readings("confirmed", "confirmed", "confirmed")
        for r in bad[:2]:
            r["card"]["watched"][0]["frame"] = "t_00m09s000.png"
        yes, no, _ = verdicts(record, bad, samples, frames)
        self.assertEqual(yes, [])
        self.assertEqual([v["verdict"] for v in no[0]["votes"]][:2],
                         ["not_visible", "not_visible"])
        # the reviewer works in the pass directory, where the still is
        # frames/<name>: that path, or ./frames/<name>, names the same file
        for named in ("frames/t_00m07s600.png", "./frames/t_00m07s600.png",
                      " t_00m07s600.png "):
            with self.subTest(named=named):
                said = readings("confirmed", "confirmed", "disagree")
                for r in said:
                    r["card"]["watched"][0]["frame"] = named
                yes, no, _ = verdicts(record, said, samples, frames)
                self.assertEqual([f["id"] for f in yes], ["W1"])
                self.assertEqual(yes[0]["frame"], "t_00m07s600.png")
        # a confirmation naming its own burst frame that was never written
        # under frames/ shows nothing
        said = readings("confirmed", "confirmed", "confirmed")
        for r in said:
            r["card"]["watched"][0]["frame"] = "burst/t_00m07s600_0228.png"
        yes, no, _ = verdicts(record, said, samples, frames)
        self.assertEqual(yes, [])
        self.assertEqual({v["verdict"] for v in no[0]["votes"]}, {"not_visible"})
        (frames / "burst").mkdir()
        (frames / "burst" / "t_00m07s600_0228.png").write_bytes(b"png")
        yes, _, _ = verdicts(record, said, samples, frames)
        self.assertEqual([f["id"] for f in yes], ["W1"])

    def test_the_brief_says_how_to_name_the_frame(self):
        record = {"model": "m", "findings": [dict(_finding(7.6, 8.0), id="W1")]}
        samples = [{"t": 7.6, "frame": "t_00m07s600.png", "watched": ["W1"]}]
        said = _watch().brief_for(record, samples)
        self.assertIn("the file name as listed in `frames`", said)

    def test_two_findings_either_side_of_a_cut_with_no_overlap_are_two(self):
        seg = _watch().Segment
        out = _watch().merge_segments([
            (seg(0.0, 30.0, 480), [_finding(29.6, 29.7, problem="a pop")]),
            (seg(30.0, 60.0, 480), [_finding(0.4, 0.5, problem="another pop")])])
        self.assertEqual([f["start"] for f in out], [29.6, 30.4])
        self.assertNotIn("segments", out[0])

    def test_the_catalog_read_failing_mid_body_is_refused(self):
        import http.client

        def broken(url, timeout):
            raise http.client.IncompleteRead(b"{\"data\": [")
        with self.assertRaises(_watch().Refused) as caught:
            _watch().catalog(broken)
        self.assertIn("could not be read", str(caught.exception))


# --------------------------------------------------------------------------- #
# sampling: a watched moment becomes a still of the pass
# --------------------------------------------------------------------------- #

class Sampling(unittest.TestCase):
    def build(self):
        return motion._build(audio_tracks=3)

    def test_watched_finding_becomes_sample(self):
        plain = qa.sample(self.build(), limit=4, voice=listening._voice())
        rows = qa.sample(self.build(), limit=4, voice=listening._voice(), watched=[
            {"id": "W1", "t": 7.6, "severity": "minor", "problem": "the super lands late"}])
        self.assertEqual(len(rows), len(plain) + 1)          # beyond --frames
        added = next(r for r in rows if r.get("watched") == ["W1"])
        self.assertEqual(added["t"], 7.6)
        self.assertEqual(added["label"], "1.3")
        self.assertTrue(added["why"].startswith("watched: W1 the super lands late"))
        self.assertEqual(added["frame"], qa.frame_name(7.6))

    def test_a_watched_moment_near_a_still_merges_into_it(self):
        plain = qa.sample(self.build(), limit=4, voice=listening._voice())
        near = plain[1]
        rows = qa.sample(self.build(), limit=4, voice=listening._voice(), watched=[
            {"id": "W1", "t": near["t"] + 0.4, "severity": "minor", "problem": "a pop"}])
        self.assertEqual(len(rows), len(plain))
        hit = next(r for r in rows if r["t"] == near["t"])
        self.assertEqual(hit["watched"], ["W1"])
        self.assertIn("watched: W1 a pop", hit["why"])

    def test_the_cap_keeps_blocking_findings_first(self):
        self.assertEqual(qa.WATCH_SAMPLES_MAX, 12)
        build = motion._build(audio_tracks=3)
        build["shots"] = [{"label": f"s{n}", "at": n * 10.0, "seconds": 10.0, "asset": "e",
                           "section": "One"} for n in range(20)]
        build["scene"]["duration"] = 200.0
        watched = [{"id": f"W{n}", "t": 2.0 + n * 10.0, "severity": "minor", "problem": "x"}
                   for n in range(1, 18)]
        watched += [{"id": "W90", "t": 193.0, "severity": "blocking", "problem": "late"},
                    {"id": "W91", "t": 196.5, "severity": "blocking", "problem": "late too"}]
        rows = qa.sample(build, limit=4, watched=watched)
        got = [w for r in rows for w in r.get("watched") or []]
        self.assertIn("W90", got)
        self.assertIn("W91", got)
        self.assertEqual(len([r for r in rows if r["why"].startswith("watched:")]),
                         qa.WATCH_SAMPLES_MAX)
        self.assertNotIn("W17", got)                          # later minor ones left out

    def test_watch_off_is_byte_identical(self):
        # digests captured before --watch existed, for the same inputs; the
        # schemas re-pinned for AUTO-146's eleventh category, editorial_identity,
        # and with it taken out they hash to the digests before it
        def sha(data):
            return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        self.assertEqual([sha(qa.sample_schema(k)) for k in range(4)], [
            "444e70f7dc3cdb484fcb0e17f2847cc9d89560a99e7e58f2924a1175faa9644a",
            "85b31a7ef009989244df789f5ccaa3c0ee9c50a5689496572cbcd8fecf2bbe84",
            "9b979106fdb2e78082e21385a017cef470f1cc3aa82aa1778feab93748e22925",
            "0c4f80a1eae8d708235702777940af146301be19a35cb8890a9059e645ece717"])
        self.assertIs(qa.sample_schema(0), qa.SCORECARD_SCHEMA)
        self.assertEqual(sha(qa.sample(self.build(), limit=4, voice=listening._voice())),
                         "4530923ca6ea0518be6d902a6a9ba65da276f50097c751d3a39137c5795cc808")
        self.assertEqual(sha(qa.sample(self.build(), limit=None, voice=listening._voice())),
                         "912f4aebea8c92f5bd17d5ba380c026f14907935159349469ad6cfe8f6586593")
        self.assertEqual(qa.sample(self.build(), limit=4, voice=listening._voice(), watched=[]),
                         qa.sample(self.build(), limit=4, voice=listening._voice()))

    def test_a_watched_moment_past_the_end_is_held_to_the_last_frame(self):
        build = self.build()
        shots = build["shots"]
        end = max(float(s["at"]) + float(s["seconds"]) for s in shots)
        fps = qa._fps(build)
        rows = qa.sample(build, limit=4, voice=listening._voice(), watched=[
            {"id": "W1", "t": end + 0.4, "severity": "minor", "problem": "a late pop"},
            {"id": "W2", "t": -0.3, "severity": "minor", "problem": "an early pop"}])
        for wid in ("W1", "W2"):
            with self.subTest(wid=wid):
                row = next(r for r in rows if wid in (r.get("watched") or []))
                self.assertGreaterEqual(row["t"], 0.0)
                self.assertLessEqual(row["t"], round(end - 1 / fps, 3))

    def test_the_watched_schema_adds_a_required_watched(self):
        for k in range(4):
            schema = qa.sample_schema(k, watched=True)
            self.assertIn("watched", schema["required"])
            items = schema["properties"]["watched"]["items"]
            self.assertEqual(items["properties"]["verdict"]["enum"],
                             ["confirmed", "not_visible", "disagree"])
            self.assertEqual(items["required"], ["id", "verdict", "frame", "why"])
        self.assertNotIn("watched", qa.SCORECARD_SCHEMA["properties"])


# --------------------------------------------------------------------------- #
# yt qa --watch
# --------------------------------------------------------------------------- #

class Verdicts(listen_tests.Recording):
    """The scorecard reviewer, answering `watched` when it is asked: each id
    in Verdicts.say with its verdict, naming the still its sample has (or
    the frame given)."""

    say: dict = {}

    def stream(self, prompt, system=None):
        listen_tests.Recording.systems.append(system)
        listen_tests.Recording.stream_calls.append({"prompt": prompt, "cwd": self.config.cwd})
        card = dict(motion.SCORECARD)
        if "Watched findings:" in prompt:
            rows = json.loads((Path(self.config.cwd) / "samples.json").read_text())
            card["watched"] = []
            for wid, (verdict, frame) in Verdicts.say.items():
                own = next((r["frame"] for r in rows if wid in (r.get("watched") or [])), "")
                card["watched"].append({"id": wid, "verdict": verdict,
                                        "frame": frame or own, "why": f"{wid}: {verdict}"})
        yield Event(type="assistant", subtype="tool_use", tool_name="Read",
                    tool_input={"file_path": "contact-sheet.png"})
        yield Event(type="assistant", text="```json\n" + json.dumps(card) + "\n```")
        yield Event(type="result", raw={})


class Watching(listen_tests.Project):
    """--watch with the gateway on, the catalog reachable and a render host named."""

    def setUp(self):
        self.node = FakeNode(self)
        super().setUp()
        harness.ENGINE = Verdicts
        Verdicts.say = {}
        env = mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel"})
        env.start()
        self.addCleanup(env.stop)
        self.fetched = []

        def fetch(url, timeout):
            self.fetched.append((url, timeout))
            return CATALOG
        p = mock.patch.object(_watch(), "_get_json", fetch)
        p.start()
        self.addCleanup(p.stop)
        # the film has sound, so even the cut as it is goes by the host,
        # which copies its picture and drops its sound
        self.host = FakeHost(self)
        self.host.segments = [_watch().Segment(0.0, 16.5, None)]
        self.node.replies(ok(_answer([_finding(7.6, 8.4, severity="blocking",
                                               problem="the super lands two seconds late")]),
                             routing={"finalProvider": "google"}))

    def args(self, **kw):
        kw.setdefault("watch", True)
        kw.setdefault("watch_model", "google/gemini-3.6-flash")
        kw.setdefault("host", "render-1")
        return super().args(**kw)

    def watch_json(self, name="pass-01"):
        return json.loads((self.root / "qa" / name / "watch.json").read_text())

    def refused(self, *needles, **kw):
        """Refused before anything: exit 1, the piece named, no render job,
        no pass directory, nothing asked of the gateway or the reviewer."""
        before, asked = self.passes(), self.node.count
        self.jobs.clear()
        listen_tests.Recording.stream_calls.clear()
        code = self.run_qa(**kw)
        self.assertEqual(code, 1, self.stderr)
        for needle in needles:
            self.assertIn(needle, self.stderr)
        self.assertIn("--watch", self.stderr)
        self.assertEqual(self.jobs, [], "a render job started")
        self.assertEqual(self.passes(), before, "a pass directory was made")
        self.assertEqual(self.node.count, asked, "the gateway was asked")
        self.assertEqual(listen_tests.Recording.stream_calls, [], "the reviewer was asked")


class WatchOff(listen_tests.Project):
    def test_watch_off_is_byte_identical(self):
        reached = []
        with mock.patch("urllib.request.urlopen", lambda *a, **k: reached.append(a)), \
                mock.patch.object(_watch().subprocess, "run",
                                  lambda *a, **k: reached.append(a)):
            old = self.args()
            self.assertFalse(hasattr(old, "watch"))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                qa.run(old)
            prompt = self.prompt().replace(str(self.root), "<root>").replace(
                str(self.work), "<work>")
            self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                             listen_tests.OFF["prompt_sha"])
            self.assertEqual(sorted(self.card()), listen_tests.OFF["card_keys"])
            # the gateway on, the flag off: the same
            self.make_project()
            with mock.patch.dict(os.environ, {"YT_GATEWAY": "vercel",
                                              "AI_GATEWAY_API_KEY": "vck_test"}):
                self.run_qa(watch=False, watch_model="google/gemini-3.6-flash")
            prompt = self.prompt().replace(str(self.root), "<root>").replace(
                str(self.work), "<work>")
            self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                             listen_tests.OFF["prompt_sha"])
            self.assertEqual(sorted(self.card()), listen_tests.OFF["card_keys"])
        self.assertEqual(reached, [])
        self.assertFalse((self.root / "qa" / "pass-01" / "watch.json").exists())

    def test_the_flags_parse_and_watch_is_off_by_default(self):
        parser = cli.build_parser()
        args = parser.parse_args(["qa", "--title", "x"])
        self.assertFalse(args.watch)
        self.assertEqual(args.watch_model, "google/gemini-3.6-flash")
        args = parser.parse_args(["qa", "--title", "x", "--watch", "--watch-model",
                                  "zai/glm-5.3-flash"])
        self.assertTrue(args.watch)
        self.assertEqual(args.watch_model, "zai/glm-5.3-flash")


class Preflight(Watching):
    def test_watch_refuses_model_without_video_input(self):
        self.refused("anthropic/claude-opus-5", "video-input",
                     watch_model="anthropic/claude-opus-5")
        self.refused("google/gemini-3.1-pro-preview", "video-input",
                     watch_model="google/gemini-3.1-pro-preview")

    def test_watch_catalog_unreachable_exits_before_render(self):
        def down(url, timeout):
            raise urllib.error.URLError("no route to host")
        with mock.patch.object(_watch(), "_get_json", down):
            self.refused("catalog", "no route to host")

    def test_watch_without_gateway_fails_fast(self):
        with mock.patch.dict(os.environ, {"YT_GATEWAY": ""}):
            self.refused("YT_GATEWAY=vercel")
        self.assertEqual(self.fetched, [], "the catalog was fetched with the gateway off")
        self.refused("RS_RENDER_HOSTS", host=None)       # YT_NO_REMOTE: no host

    def test_watch_refuses_same_maker_as_author(self):
        self.put("vfx.json", {"engine": {"model": "sonnet", "by_model": {}}, "authored": [
            {"n": 1, "engine": {"model": "opus", "by_model": {"google/gemini-3.7-flash": {}}}}]})
        self.refused("google/gemini-3.7-flash", "google")

    def test_a_model_without_verified_providers_is_refused(self):
        self.refused("WATCH_PROVIDERS", watch_model="zai/glm-5.3-flash")

    def test_the_authors_are_read_where_qa_reads_them(self):
        # --script FILE (in_script) is the script qa uses: its author counts
        elsewhere = self.tmp / "elsewhere-script.json"
        elsewhere.write_text(json.dumps({"engine": {"model": "google/gemini-3.7-flash",
                                                    "by_model": {}}}))
        self.refused("google/gemini-3.7-flash", in_script=str(elsewhere))

    def test_no_model_takes_the_reviewer_maker_from_the_engine(self):
        project = workspace.Project(self.root)
        # script.json is Anthropic's (sonnet); the claude CLI's default is too
        prepared = _watch().preflight(self.args(model=None), project, home=self.root)
        self.assertIs(prepared.reviewer_same_maker, True)
        with mock.patch.object(harness, "ENGINE_NAME", "codex"):
            prepared = _watch().preflight(self.args(model=None), project, home=self.root)
        self.assertIs(prepared.reviewer_same_maker, False)
        with mock.patch.object(harness, "ENGINE_NAME", "bridge"):
            prepared = _watch().preflight(self.args(model=None), project, home=self.root)
        self.assertIsNone(prepared.reviewer_same_maker)

    def test_a_build_with_no_shots_stops_before_the_watch(self):
        build = json.loads((self.root / "build.json").read_text())
        build["shots"] = []
        self.put("build.json", build)
        later = (self.root / "scene.xml").stat().st_mtime + 60
        os.utime(self.root / "build.json", (later, later))
        code = self.run_qa()
        self.assertEqual(code, 1, self.stderr)
        self.assertIn("no shots to sample", self.stderr)
        self.assertEqual(self.node.count, 0)
        self.assertEqual(self.passes(), [])
        self.assertEqual(self.fetched, [])

    def test_local_does_not_move_the_prep_here(self):
        project = workspace.Project(self.root)
        prepared = _watch().preflight(self.args(local=True), project, home=self.root)
        self.assertEqual(prepared.host, "render-1")

    def test_watch_harness_config(self):
        prepared = _watch().Prepared(model="google/gemini-3.6-flash", providers=["google"])
        project = workspace.Project(self.root)
        watcher = _watch().make_harness(project, prepared, self.args())
        self.assertEqual(watcher.engine, "gateway")
        self.assertEqual(watcher.gateway["providers"],
                         list(_watch().WATCH_PROVIDERS["google/gemini-3.6-flash"]))
        self.assertNotIn("has", watcher.gateway)
        self.assertEqual(watcher.gateway["max_media_mb"] * 1024 * 1024,
                         _watch().WATCH_MAX_BYTES)
        self.assertEqual(watcher.timeout, _watch().WATCH_TIMEOUT)
        self.assertFalse(watcher.feedback)
        self.assertEqual(watcher.model, "google/gemini-3.6-flash")
        # the scorecard's reviewer is built as today: --model, the global
        # engine, no gateway config
        self.run_qa()
        card = self.card()
        self.assertEqual(card["engine"]["model"], "sonnet")
        self.assertEqual(card["watch_engine"]["model"], "google/gemini-3.6-flash")
        self.assertTrue(listen_tests.Recording.stream_calls, "the reviewer was not asked")
        self.assertNotIn("gateway", json.dumps(card["engine"]))


class Run(Watching):
    def test_a_watched_pass(self):
        Verdicts.say = {"W1": ("confirmed", None)}
        code = self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["status"], "watched", self.stderr)
        self.assertEqual(record["plan"]["kind"], "as_is")
        self.assertEqual([f["id"] for f in record["findings"]], ["W1"])
        self.assertEqual(record["findings"][0]["start"], 7.6)
        self.assertEqual(record["catalog_row"]["id"], "google/gemini-3.6-flash")
        self.assertEqual(record["calls"][0]["model"], "google/gemini-3.6-flash")
        self.assertEqual(record["calls"][0]["generation_id"], "gen_01TEST")
        self.assertEqual(record["calls"][0]["provider"], "google")
        # the sound never leaves: the picture is copied as it is on the host
        self.assertEqual(record["plan"]["host"], "render-1")
        self.assertEqual([s["audio"] for s in record["plan"]["segments"]], [False])
        self.assertIsNone(record["plan"]["segments"][0]["scale"])
        script = next(c for c in self.host.calls if c[0] == "ssh")[-1]
        self.assertIn(" -an ", script)
        self.assertIn("-c:v copy", script)
        self.assertNotIn("scale=", script)
        self.assertNotIn("libx264", script)
        # the reviewer (--model sonnet) is Anthropic's, like script.json's author:
        # recorded, not refused (open question 3)
        self.assertIs(record["reviewer_same_maker"], True)
        self.assertEqual(self.fetched, [(_watch().CATALOG_URL, 20.0)])
        # the question never names the pass (the media are digested by
        # their bytes, so the segment's path in it does not matter)
        request = self.node.requests()[0]
        self.assertNotIn("pass-01", request["prompt"] + request["instructions"])
        samples = json.loads((self.root / "qa" / "pass-01" / "samples.json").read_text())
        self.assertTrue(any(r.get("watched") == ["W1"] for r in samples))
        card = self.card()
        self.assertIn("Watched findings:", self.prompt())
        mine = [f for f in card["findings"] if f.get("source") == "watched:W1"]
        self.assertEqual(len(mine), 1)
        self.assertTrue(any("the super lands two seconds late" in b for b in card["blocking"]))
        self.assertEqual(card["watch"]["status"], "watched")
        self.assertEqual(code, 1)                          # a blocking finding stands

    def test_the_rhythm_scan_rides_the_watchs_upload(self):
        # AUTO-141: with --watch the cut is already on the host, so the
        # rhythm scan reads it there: one upload, one directory removed,
        # and rhythm.json says the upload was reused.
        from ytstudio import rhythm
        qa.RHYTHM_SCAN = rhythm.scan_remote
        scores = "".join(f"frame:{k} pts:{k} pts_time:{k / 4:g}\n"
                         f"lavfi.scene_score={0.05 if k % 8 else 0.0}\n"
                         for k in range(66))
        plain = self.host.run

        def run(argv, *a, **kw):
            if argv and argv[0] == "ssh" and "lavfi.scene_score" in argv[-1]:
                self.host.calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, "", scores)
            return plain(argv, *a, **kw)
        with mock.patch.object(_watch().subprocess, "run", run):
            self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["status"], "watched", self.stderr)
        ups = [c for c in self.host.calls if c[0] == "rsync" and c[-1].endswith("/cut.mp4")]
        self.assertEqual(len(ups), 1, self.host.calls)
        root = ups[0][-1].split(":", 1)[1].rsplit("/", 1)[0]
        scan = next(c for c in self.host.calls
                    if c[0] == "ssh" and "lavfi.scene_score" in c[-1])
        self.assertIn(f"{root}/cut.mp4", scan[-1])
        self.assertEqual(self.host.removed, [("render-1", root)])
        kept = json.loads((self.root / "qa" / "pass-01" / "rhythm.json").read_text())
        self.assertEqual(kept["scan"]["upload"], "reused")
        self.assertEqual(kept["scan"]["host"], "render-1")
        self.assertEqual(kept["scan"]["upload_bytes"],
                         (self.root / "video.mp4").stat().st_size)
        self.assertIn("upload_seconds", kept["scan"])
        self.assertIsNotNone(kept["cv"]["events"])
        self.assertFalse([n for n in self.card()["not_evidenced"]
                          if n.startswith("rhythm: hold")])

    def test_a_cut_without_sound_is_sent_as_it_is(self):
        self.make_project(audio_tracks=0)
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["status"], "watched", self.stderr)
        self.assertEqual(record["plan"]["kind"], "as_is")
        self.assertIsNone(record["plan"]["host"])
        self.assertEqual(self.host.calls, [])
        self.assertEqual(record["plan"]["segments"][0]["file"],
                         str(self.root / "video.mp4"))
        self.assertEqual([s["audio"] for s in record["plan"]["segments"]], [False])

    def test_reviewer_confirms_naming_the_frames_path(self):
        # the reviewer's cwd is the pass directory: frames/<still> is its path
        Verdicts.say = {"W1": ("confirmed", None)}
        real = Verdicts.stream

        def stream(engine, prompt, system=None):
            for event in real(engine, prompt, system):
                if event.text and '"watched"' in event.text:
                    body = json.loads(event.text.strip("`\njson"))
                    for w in body["watched"]:
                        w["frame"] = "frames/" + w["frame"]
                    event = Event(type="assistant",
                                  text="```json\n" + json.dumps(body) + "\n```")
                yield event
        with mock.patch.object(Verdicts, "stream", stream):
            code = self.run_qa()
        card = self.card()
        self.assertEqual(card["watched"]["confirmed"], ["W1"], self.stderr)
        self.assertEqual(code, 1)

    def test_scorecard_carries_watched_verdicts(self):
        self.node.replies(ok(_answer([
            _finding(7.6, 8.4, severity="blocking", problem="the super lands late"),
            _finding(12.4, 12.6, problem="a two-frame pop")])))
        Verdicts.say = {"W1": ("confirmed", "t_99m59s999.png"), "W2": ("disagree", None)}
        self.run_qa()
        card = self.card()
        self.assertEqual([f for f in card["findings"]
                          if str(f.get("source", "")).startswith("watched")], [])
        self.assertFalse(any("the super lands late" in b for b in card["blocking"]))
        self.assertEqual([f["id"] for f in card["watched"]["not_confirmed"]], ["W1", "W2"])
        self.assertEqual({v["verdict"] for v in card["watched"]["not_confirmed"][0]["votes"]},
                         {"not_visible"})
        md = (self.root / "qa" / "pass-01" / "findings.md").read_text()
        self.assertIn("watched, not confirmed: W1", md)
        self.assertIn("watched, not confirmed: W2", md)

    def test_stale_video_not_watched(self):
        early = (self.root / "scene.xml").stat().st_mtime - 60
        os.utime(self.root / "video.mp4", (early, early))
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["status"], "not_watched")
        self.assertEqual(self.node.count, 0)
        card = self.card()
        line = next(n for n in card["not_evidenced"] if n.startswith("watch:"))
        self.assertIn("video.mp4 predates scene.xml", line)
        self.assertIn("yt render", line)
        self.assertNotIn("Watched findings:", self.prompt())

    def test_watch_out_of_range_asked_again_then_recorded(self):
        self.node.replies(ok(_answer([_finding(3.0, 40.0)])))
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(self.node.count, 2)
        self.assertIn("not inside the video", json.dumps(self.node.requests()[1]))
        self.assertEqual(record["findings"], [])
        self.assertEqual(record["status"], "failed")
        self.assertTrue(any("not watched" in n for n in self.card()["not_evidenced"]))
        self.assertEqual([d["name"] for d in record["decisions"]],
                         ["watch-s01", "watch-s01-again"])

    def test_a_twice_failed_answer_is_not_kept_for_the_next_run(self):
        self.node.replies(ok(_answer([_finding(3.0, 40.0)])))
        self.run_qa()
        self.assertEqual(self.node.count, 2)
        decisions = self.root / harness.DECISIONS_DIR / "qa"
        self.assertFalse((decisions / "watch-s01.json").exists())
        self.assertFalse((decisions / "watch-s01-again.json").exists())
        # the next run asks the watcher again rather than replaying the failure
        self.node.replies(ok(_answer([_finding(3.0, 4.0)])))
        self.run_qa()
        self.assertEqual(self.node.count, 3)
        self.assertEqual(self.watch_json("pass-02")["status"], "watched")

    def test_watch_duration_check_catches_dropped_video(self):
        self.node.replies(ok(_answer([_finding(1.0, 2.0)], seconds=0)))
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(self.node.count, 2)
        self.assertEqual(record["plan"]["segments"][0]["status"], "unwatched")
        self.assertIn("watched_seconds", record["plan"]["segments"][0]["why"])
        self.assertEqual(record["findings"], [])

    def split(self):
        """The cut split in pieces on the fake host (WATCH_MAX_SECONDS 5)."""
        host = FakeHost(self)
        host.segments = _watch().watch_plan(
            16.5, 6, motion._build()["shots"], max_bytes=_watch().WATCH_MAX_BYTES,
            max_seconds=5.0, kbps=_watch().WATCH_KBPS)
        p = mock.patch.object(_watch(), "WATCH_MAX_SECONDS", 5.0)
        p.start()
        self.addCleanup(p.stop)
        return host

    def test_a_split_cut_is_watched_piece_by_piece(self):
        host = self.split()
        self.node.replies(*[ok(_answer([_finding(0.5, 0.9)], seconds=s.end - s.start))
                            for s in host.segments])
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["plan"]["kind"], "downscale_split")
        self.assertEqual(self.node.count, len(host.segments))
        self.assertEqual([f["start"] for f in record["findings"]],
                         [round(s.start + 0.5, 3) for s in host.segments])
        self.assertTrue(all(s["audio"] is False for s in record["plan"]["segments"]))
        self.assertEqual(len(host.removed), 1)

    def test_watch_402_stops_remaining_segments(self):
        host = self.split()
        self.node.replies(err("gateway", 1, status=402,
                              message="quota_for_entity_exceeded: budget exceeded"))
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(self.node.count, 1)
        states = [s["status"] for s in record["plan"]["segments"]]
        self.assertEqual(states, ["unwatched"] * len(host.segments))
        self.assertTrue(all("not asked" in s["why"] for s in record["plan"]["segments"][1:]))
        self.assertTrue((self.root / "qa" / "pass-01" / "scorecard.json").is_file())

    def test_a_failed_call_leaves_its_segment_unwatched_and_the_watch_goes_on(self):
        host = self.split()
        self.node.replies(err("gateway", 1, status=401, message="Unauthorized: invalid key"),
                          *[ok(_answer([], seconds=s.end - s.start)) for s in host.segments[1:]])
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(self.node.count, len(host.segments))
        first, *rest = record["plan"]["segments"]
        self.assertEqual(first["status"], "unwatched")
        self.assertIn("401", first["why"])
        self.assertEqual([s["status"] for s in rest], ["watched"] * len(rest))
        self.assertEqual(record["status"], "watched")
        self.assertEqual(record["reason"], f"1 of {len(host.segments)} segment(s) not watched")

    def test_a_402_stops_the_watch_by_its_status_alone(self):
        host = self.split()
        self.node.replies(err("gateway", 1, status=402, message="Payment required"))
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(self.node.count, 1)
        self.assertEqual([s["status"] for s in record["plan"]["segments"]],
                         ["unwatched"] * len(host.segments))
        self.assertTrue(all("not asked" in s["why"] for s in record["plan"]["segments"][1:]))

    def test_prepare_output_over_limit_not_sent(self):
        host = self.split()
        host.sizes = {3: _watch().WATCH_MAX_BYTES + 1}
        self.run_qa()
        record = self.watch_json()
        self.assertEqual(record["status"], "failed")
        self.assertIn("over WATCH_MAX_BYTES", record["reason"])
        self.assertEqual(self.node.count, 0)
        self.assertTrue((self.root / "qa" / "pass-01" / "scorecard.json").is_file())

    def test_same_cut_reuses_previous_watch(self):
        self.run_qa()
        self.assertEqual(self.node.count, 1)
        self.run_qa()
        record = self.watch_json("pass-02")
        self.assertEqual(record["reused_from"], "pass-01")
        self.assertEqual(self.node.count, 1)
        self.assertEqual([f["id"] for f in record["findings"]], ["W1"])
        self.run_qa(fresh=True)
        self.assertEqual(self.watch_json("pass-03")["reused_from"], None)
        self.assertEqual(self.node.count, 2)

    def test_a_watch_made_otherwise_is_not_reused(self):
        self.run_qa()
        self.assertEqual(self.node.count, 1)
        first = self.watch_json()
        changes = {
            "constants": lambda r: r["constants"].update(height=720),
            "role": lambda r: r.update(role_sha256="0" * 64),
            "model": lambda r: r.update(model="zai/glm-5.3-flash"),
            "providers": lambda r: r.update(providers=["vertex"]),
            "cut": lambda r: r["cut"].update(sha256="f" * 64),
            "partial": lambda r: r["plan"]["segments"][0].update(status="unwatched"),
        }
        for n, (what, change) in enumerate(changes.items(), 2):
            with self.subTest(what=what):
                # every earlier record is the first one, changed in one way
                for old in (self.root / "qa").glob("pass-*/watch.json"):
                    record = json.loads(json.dumps(first))
                    change(record)
                    old.write_text(json.dumps(record))
                asked = self.node.count
                self.run_qa()
                self.assertIsNone(self.watch_json(f"pass-{n:02d}")["reused_from"])
                # served from decisions/qa/ (the same bytes, the same question)
                # or asked: either way not copied from the changed record
                self.assertGreaterEqual(self.node.count, asked)
                self.assertTrue(self.watch_json(f"pass-{n:02d}")["decisions"])

    def test_reuse_with_watch_reads_watch_json(self):
        Verdicts.say = {"W1": ("confirmed", None)}
        self.run_qa()
        asked = self.node.count
        code = self.run_qa(reuse="pass-01", fresh=True)
        self.assertIn(code, (0, 1), self.stderr)
        self.assertEqual(self.node.count, asked)
        self.assertEqual(self.fetched, [(_watch().CATALOG_URL, 20.0)])   # the first run's only
        self.assertIn("Watched findings:", self.prompt())
        self.run_qa(watch=False)
        self.refused("pass-02 was not watched; run without --reuse to watch", reuse="pass-02")

    def test_watch_cost_booked_and_estimated(self):
        plain = dict(spend.flag_increments(self.root, "qa", ["--watch"]))
        self.assertGreater(plain["--watch"], 0)
        self.assertGreater(_watch().watch_usd(self.root, []), 0)
        row = next(r for r in spend.stage_estimates(self.root, [], extras={"qa": ["--watch"]})
                   if r["stage"] == "qa")
        self.assertIn("--watch", row["source"])
        self.node.replies(ok(_answer([_finding(7.6, 8.4)]), cost="0.012"))
        self.run_qa()
        record = self.watch_json()
        row = next(d for d in record["decisions"] if d["name"] == "watch-s01")
        self.assertAlmostEqual(row["cost_usd"], 0.012)
        card = self.card()
        self.assertEqual(card["watch_engine"]["decisions"][0]["name"], "watch-s01")
        reviewer = spend.from_report(card["engine"])["cost_usd"]
        self.assertAlmostEqual(card["usage"]["cost_usd"], reviewer + 0.012, places=6)
        self.assertIn("google/gemini-3.6-flash", card["usage"]["by_model"])
        qa_json = json.loads((self.root / "qa.json").read_text())
        self.assertEqual(qa_json["usage"], card["usage"])
        # like a listen, a past watch is no part of the next pass's base,
        # so the flag prices the next watch each time it is asked
        self.assertEqual(dict(spend.flag_increments(self.root, "qa", ["--watch"])),
                         plain)
        self.assertAlmostEqual(qa.pass_usd(card),
                               round(reviewer / card["scoring"]["asked"] * qa.QA_SAMPLES, 2))


class PassCost(unittest.TestCase):
    def test_a_past_watch_is_left_out_of_the_pass_estimate(self):
        card = {"usage": {"cost_usd": 1.5}, "scoring": {"asked": 3},
                "engine": {"decisions": []},
                "watch_engine": {"cost_usd": 0.5, "decisions": [
                    {"name": "watch-s01", "cost_usd": 0.5, "cached": False}]}}
        self.assertEqual(qa.pass_usd(card), round(1.0 / 3 * qa.QA_SAMPLES, 2))
        # neither scaled by the samples nor counted once
        card["scoring"]["asked"] = 1
        self.assertEqual(qa.pass_usd(card), round(1.0 * qa.QA_SAMPLES, 2))


if __name__ == "__main__":
    unittest.main()
