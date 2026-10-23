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

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import importlib  # noqa: E402

from ytstudio import doctor, workspace  # noqa: E402

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
            ("no QA pass has scored this render", f"yt qa --project {project.root}")])
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


if __name__ == "__main__":
    unittest.main()
