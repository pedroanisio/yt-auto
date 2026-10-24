"""rhythm.py and its place in qa.py (AUTO-141): the film's rhythm, measured.

Direction 12 asks for no static hold over 8 s while the voice runs, an
opening whose change rate is not below the film's median, and shot lengths
that are not metronomic (a coefficient of variation under 0.3). Direction 25
asks for 150-190 words a minute of voiced time, failing under 130 or over
200. These pin the measures, the findings they file and how those block or
cap: all pure, with no ffmpeg, no ssh and no model.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests import hermetic
from ytstudio import qa, render, rhythm

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rhythm" / "scene_scores.txt"
FPS = rhythm.value("scan_fps")
LEVEL = 0.05        # a frame that changed: well over CHANGE_SCORE
QUIET = 0.0


def calibrated(on: bool = True):
    """RHYTHM's calibration flag, set for one test."""
    return mock.patch.dict(rhythm.RHYTHM["calibrated"], {"value": on})


def film(lengths, *, functions=None, asset="effect", section=lambda n: "One"):
    """build.json: one shot a length, end to end, labelled 1.1, 1.2..."""
    shots, t = [], 0.0
    for n, seconds in enumerate(lengths, 1):
        shots.append({"label": f"1.{n}", "at": round(t, 3), "seconds": float(seconds),
                      "asset": asset, "section": section(n),
                      "function": (functions or {}).get(n, "")})
        t += seconds
    return {"scene": {"duration": round(t, 3), "fps": 30}, "shots": shots}


def series(duration, *, quiet=(), onsets=(), level=LEVEL, base=None):
    """(t, score) at the scan's rate: `level` everywhere (the picture always
    changing) unless `base` is QUIET, with `quiet` [a, b] runs under
    CHANGE_SCORE and `onsets` single frames of change."""
    out = []
    for k in range(int(round(duration * FPS))):
        t = round(k / FPS, 3)
        score = level if base is None else base
        if any(a <= t <= b for a, b in quiet):
            score = QUIET
        if any(abs(t - o) < 1e-6 for o in onsets):
            score = level
        out.append((t, score))
    return out


def take(label, section, at, n, wpm, *, timing="exact", gap_after=None, gap=0.0):
    """A voice.json take of `n` words read back to back at `wpm`, a `gap`
    of silence after word `gap_after`; word times count from the take."""
    d = 60.0 / wpm
    words, t = [], 0.0
    for i in range(n):
        words.append({"text": f"w{i}", "start": round(t, 3), "end": round(t + d, 3)})
        t += d
        if gap_after == i:
            t += gap
    return {"label": label, "section": section, "at": at, "seconds": round(t, 3),
            "words": words, "timing": timing,
            "text": " ".join(w["text"] for w in words)}


def narrated(build, wpm=170.0):
    """A take on every shot, from half a second in to half a second before
    its end: narration runs over the whole film."""
    out = []
    for shot in build["shots"]:
        n = int((shot["seconds"] - 1.0) * wpm / 60.0)
        row = take(shot["label"], shot["section"], shot["at"], n, wpm)
        for w in row["words"]:
            w["start"] = round(w["start"] + 0.5, 3)
            w["end"] = round(w["end"] + 0.5, 3)
        out.append(row)
    return out


def kinds(found, kind):
    return [f for f in found if f["kind"] == kind]


# --------------------------------------------------------------------------- #
# the shot-length spread
# --------------------------------------------------------------------------- #

class Spread(unittest.TestCase):
    def test_equal_shots_with_no_scan_are_metronomic_on_the_shots_basis(self):
        build = film([4, 4, 4, 4, 4])
        self.assertEqual(rhythm.shot_lengths(build), [4.0] * 5)
        self.assertEqual(rhythm.interval_cv(rhythm.shot_lengths(build)), 0.0)
        found = kinds(rhythm.rhythm_findings(build, []), "metronomic")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["basis"], "shots")
        self.assertEqual(found[0]["owner"], "script")
        self.assertEqual(found[0]["severity"], "blocking")
        self.assertEqual(found[0]["scope"], "film")
        self.assertEqual(found[0]["shot"], "1.1")
        self.assertEqual(found[0]["timestamp"], 0.0)
        self.assertIn("CV 0.00 (< 0.3) over 5 intervals (basis shots)", found[0]["problem"])

    def test_varied_shots_file_nothing_and_three_shots_are_not_evidence(self):
        self.assertGreater(rhythm.interval_cv([2, 6, 3, 9, 4]), 0.3)
        self.assertEqual(kinds(rhythm.rhythm_findings(film([2, 6, 3, 9, 4]), []),
                               "metronomic"), [])
        self.assertIsNone(rhythm.interval_cv([4, 4, 4]))
        self.assertEqual(rhythm.rhythm_findings(film([4, 4, 4]), []), [])

    def test_a_zero_length_shot_is_no_shot(self):
        build = film([4, 0, 5])
        self.assertEqual(rhythm.shot_lengths(build), [4.0, 5.0])

    def test_builds_inside_equal_shots_clear_the_events_basis(self):
        build = film([4, 4, 4, 4, 4])
        scores = series(20.0, base=QUIET, onsets=(1.0, 9.0, 10.5))
        events = rhythm.change_events(build, scores, FPS)
        self.assertEqual(events, [1.0, 4.0, 8.0, 9.0, 10.5, 12.0, 16.0])
        with calibrated(True):
            block = rhythm.measure(build, [], scores, fps=FPS)
        self.assertEqual(block["cv"]["basis"], "events")
        self.assertGreaterEqual(block["cv"]["events"], 0.3)
        self.assertEqual(kinds(block["findings"], "metronomic"), [])

    def test_an_onset_right_on_an_authored_cut_is_that_cut(self):
        build = film([4, 4, 4, 4, 4])
        scores = series(20.0, base=QUIET, onsets=(4.0, 8.25))
        self.assertEqual(rhythm.change_events(build, scores, FPS), [4.0, 8.0, 12.0, 16.0])


# --------------------------------------------------------------------------- #
# holds under narration, and where they block
# --------------------------------------------------------------------------- #

#: 120 s: 1.1 the hook (0-12), 1.2 (12-32), 1.3 (32-62), 1.4 (62-87), 1.5 last (87-120).
LENGTHS = [12, 20, 30, 25, 33]


class Holds(unittest.TestCase):
    def test_a_run_of_unchanged_frames_is_a_hold_to_the_frame(self):
        scores = series(60.0, quiet=[(40.25, 50.0)])
        self.assertEqual(rhythm.holds(scores, FPS), [(40.0, 50.0)])

    def test_ten_seconds_under_narration_is_one_hold_finding(self):
        build = film(LENGTHS, functions={1: "hook"})
        with calibrated(True):
            found = rhythm.rhythm_findings(build, narrated(build),
                                           series(120.0, quiet=[(40.25, 50.0)]), fps=FPS)
        holds = kinds(found, "hold")
        self.assertEqual(len(holds), 1, found)
        self.assertEqual(holds[0]["timestamp"], 40.0)
        self.assertEqual(holds[0]["shot"], "1.3")
        self.assertEqual(holds[0]["scope"], "shot")
        self.assertEqual(holds[0]["owner"], "vfx")
        self.assertIn("no visual change for 10.0 s (40.0-50.0 s) while the narration runs",
                      holds[0]["problem"])
        self.assertEqual(holds[0]["severity"], "minor")      # one middle shot

    def test_the_same_run_in_a_wordless_gap_or_a_shorter_run_files_nothing(self):
        build = film(LENGTHS, functions={1: "hook"})
        takes = [t for t in narrated(build) if t["label"] != "1.3"]
        with calibrated(True):
            silent = rhythm.rhythm_findings(build, takes, series(120.0, quiet=[(40.25, 50.0)]),
                                            fps=FPS)
            short = rhythm.rhythm_findings(build, narrated(build),
                                           series(120.0, quiet=[(40.25, 47.0)]), fps=FPS)
        self.assertEqual(kinds(silent, "hold"), [])
        self.assertEqual(kinds(short, "hold"), [])

    def test_a_shot_drawn_by_the_build_owns_its_hold(self):
        build = film(LENGTHS, asset="image")
        with calibrated(True):
            found = rhythm.rhythm_findings(build, narrated(build),
                                           series(120.0, quiet=[(40.25, 50.0)]), fps=FPS)
        self.assertEqual(kinds(found, "hold")[0]["owner"], "build")


class Edge(unittest.TestCase):
    def test_the_edge_is_the_opening_the_last_shot_the_hook_and_the_payoff(self):
        build = film(LENGTHS, functions={3: "payoff", 4: "hook"})
        self.assertEqual(rhythm.edge_labels(build, 30.0), {"1.1", "1.2", "1.3", "1.4", "1.5"})
        self.assertEqual(rhythm.edge_labels(film(LENGTHS), 30.0), {"1.1", "1.2", "1.5"})

    def held(self, quiet, functions=None):
        build = film(LENGTHS, functions=functions or {})
        with calibrated(True):
            return kinds(rhythm.rhythm_findings(build, narrated(build),
                                                series(120.0, quiet=quiet), fps=FPS), "hold")

    def test_a_hold_on_an_unsampled_hook_shot_is_blocking(self):
        # No sample row anywhere: the edge is read from build.json alone.
        found = self.held([(40.25, 50.0)], functions={3: "hook"})
        self.assertEqual([f["severity"] for f in found], ["blocking"])
        found = self.held([(1.25, 11.0)], functions={1: "hook"})
        self.assertEqual([f["severity"] for f in found], ["blocking"])

    def test_a_hold_on_the_last_shot_or_on_two_shots_is_blocking(self):
        self.assertEqual([f["severity"] for f in self.held([(95.25, 105.0)])], ["blocking"])
        two = self.held([(40.25, 50.0), (65.25, 75.0)])
        self.assertEqual(sorted(f["shot"] for f in two), ["1.3", "1.4"])
        self.assertEqual({f["severity"] for f in two}, {"blocking"})


# --------------------------------------------------------------------------- #
# the opening
# --------------------------------------------------------------------------- #

def slow_film(duration):
    """One 30 s opening shot, then a cut every 6 s, the picture always
    changing: the opening has no event, every later window five."""
    build = film([30] + [6] * int((duration - 30) / 6))
    return rhythm.rhythm_findings(build, [], series(duration), fps=FPS)


class Opening(unittest.TestCase):
    def test_the_rates_are_per_minute_over_full_thirty_second_windows(self):
        events = [10.0, 25.0] + [30.0 + 6 * k for k in range(15)]
        self.assertEqual(rhythm.opening_rate(events, 120.0), 4.0)
        self.assertEqual(rhythm.film_median_rate(events, 120.0), 10.0)
        self.assertIsNone(rhythm.film_median_rate(events, 80.0))

    def test_an_opening_slower_than_the_film_is_slow_opening(self):
        with calibrated(True):
            found = kinds(slow_film(120.0), "slow-opening")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["owner"], "vfx")
        self.assertEqual(found[0]["severity"], "blocking")
        self.assertEqual(found[0]["scope"], "film")
        self.assertIn("opening 30 s has 0.0 changes/min against a film median of 10.0",
                      found[0]["problem"])

    def test_a_film_under_ninety_seconds_has_no_median(self):
        with calibrated(True):
            self.assertEqual(kinds(slow_film(78.0), "slow-opening"), [])


# --------------------------------------------------------------------------- #
# the calibration gate
# --------------------------------------------------------------------------- #

class Calibration(unittest.TestCase):
    def test_the_shipped_scan_is_not_calibrated(self):
        self.assertIs(rhythm.calibrated(), False)
        self.assertIn("TO VERIFY", rhythm.RHYTHM["change_score"]["strength"])
        self.assertIn("calibration", rhythm.RHYTHM["calibrated"]["note"])

    def test_before_calibration_a_scan_finding_is_minor_and_says_to_verify(self):
        build = film(LENGTHS, functions={1: "hook"})
        with calibrated(False):
            hook = kinds(rhythm.rhythm_findings(build, narrated(build),
                                                series(120.0, quiet=[(1.25, 11.0)]), fps=FPS),
                         "hold")
            opening = kinds(slow_film(120.0), "slow-opening")
            metronomic = kinds(rhythm.rhythm_findings(film([4] * 6), []), "metronomic")
        for f in hook + opening:
            self.assertEqual(f["severity"], "minor", f)
            self.assertIn("(uncalibrated scan: TO VERIFY)", f["why"])
        self.assertEqual(len(hook), 1)
        self.assertEqual(len(opening), 1)
        self.assertEqual([f["severity"] for f in metronomic], ["blocking"])

    def test_an_uncalibrated_scan_that_clears_equal_shots_leaves_them_minor(self):
        build = film([4, 4, 4, 4, 4])
        scores = series(20.0, base=QUIET, onsets=(1.0, 9.0, 10.5))
        with calibrated(False):
            found = kinds(rhythm.rhythm_findings(build, [], scores, fps=FPS), "metronomic")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["basis"], "shots")
        self.assertEqual(found[0]["severity"], "minor")
        self.assertIn("CV 0.00", found[0]["why"])
        self.assertIn("CV 0.52", found[0]["why"])

    def test_an_uncalibrated_scan_that_confirms_equal_shots_still_blocks(self):
        build = film([4, 4, 4, 4, 4])
        with calibrated(False):
            found = kinds(rhythm.rhythm_findings(build, [], series(20.0, base=QUIET), fps=FPS),
                          "metronomic")
        self.assertEqual([(f["basis"], f["severity"]) for f in found], [("shots", "blocking")])


# --------------------------------------------------------------------------- #
# words a minute
# --------------------------------------------------------------------------- #

def trap_film():
    return film([10, 10, 10, 10], section=lambda n: "the trap" if n > 2 else "One")


class Wpm(unittest.TestCase):
    def found(self, wpm, timing="exact"):
        takes = [take("1.3", "the trap", 20.0, 40, wpm, timing=timing)]
        return rhythm.measure(trap_film(), takes)

    def test_the_bands(self):
        self.assertEqual(kinds(self.found(170)["findings"], "wpm"), [])
        for wpm, severity in ((214, "blocking"), (145, "minor"), (120, "blocking")):
            with self.subTest(wpm=wpm):
                found = kinds(self.found(wpm)["findings"], "wpm")
                self.assertEqual([f["severity"] for f in found], [severity])
                self.assertEqual(found[0]["owner"], "voice")
                self.assertEqual(found[0]["scope"], "section")
                self.assertEqual(found[0]["section"], "the trap")
                self.assertEqual(found[0]["shot"], "1.3")
                self.assertEqual(found[0]["timestamp"], 20.0)
        self.assertIn("section 'the trap' reads at 214 wpm (fail band 130-200)",
                      kinds(self.found(214)["findings"], "wpm")[0]["problem"])

    def test_a_long_pause_is_left_out_of_the_voiced_seconds(self):
        row = take("1.1", "One", 0.0, 20, 200, gap_after=9, gap=1.4)
        [got] = rhythm.section_wpm([row])
        self.assertAlmostEqual(got["voiced_seconds"], 6.0, places=2)
        self.assertAlmostEqual(got["left_out_seconds"], 1.4, places=2)
        self.assertAlmostEqual(got["wpm"], 200.0, delta=0.5)
        self.assertEqual(got["timing"], "exact")

    def test_an_estimated_take_keeps_its_pauses(self):
        row = take("1.1", "One", 0.0, 20, 200, gap_after=9, gap=1.4, timing="estimated")
        [got] = rhythm.section_wpm([row])
        self.assertAlmostEqual(got["voiced_seconds"], 7.4, places=2)
        self.assertEqual(got["left_out_seconds"], 0.0)
        self.assertEqual(got["timing"], "estimated")

    def test_a_slow_estimated_section_is_not_evidenced_and_files_nothing(self):
        block = self.found(120, timing="estimated")
        self.assertEqual(kinds(block["findings"], "wpm"), [])
        row = next(r for r in block["not_evidenced"] if r["measure"] == "wpm")
        self.assertEqual(row["section"], "the trap")
        self.assertIn("pauses cannot be removed without aligned timings", row["why"])

    def test_a_fast_estimated_section_still_fails(self):
        found = kinds(self.found(214, timing="estimated")["findings"], "wpm")
        self.assertEqual([f["severity"] for f in found], ["blocking"])

    def test_a_section_takes_its_least_exact_timing(self):
        rows = rhythm.section_wpm([take("1.1", "One", 0.0, 20, 170),
                                   take("1.2", "One", 8.0, 20, 170, timing="none")])
        self.assertEqual(rows[0]["timing"], "none")
        self.assertEqual(rows[0]["takes"], 2)


# --------------------------------------------------------------------------- #
# the shape every finding has
# --------------------------------------------------------------------------- #

class Shape(unittest.TestCase):
    def every_kind(self):
        build = film([30] + [6] * 15, functions={1: "hook"})
        takes = narrated(build, wpm=214)
        with calibrated(True):
            found = rhythm.rhythm_findings(build, takes, series(120.0, quiet=[(1.25, 11.0)]),
                                           fps=FPS)
        found += rhythm.rhythm_findings(film([4] * 6), [])
        return found

    def test_every_finding_has_a_float_timestamp_a_shot_and_a_scope(self):
        found = self.every_kind()
        self.assertEqual({f["kind"] for f in found}, set(rhythm.KINDS))
        for f in found:
            with self.subTest(kind=f["kind"]):
                self.assertIsInstance(f["timestamp"], float)
                self.assertTrue(f["shot"])
                self.assertIn(f["scope"], ("shot", "section", "film"))
                for key in ("problem", "why", "root_cause", "fix", "owner", "severity"):
                    self.assertTrue(f[key], key)
        self.assertEqual(rhythm.KINDS, qa.RHYTHM_KINDS)

    def test_blocking_findings_render_as_lines_and_in_findings_md(self):
        found = [f for f in self.every_kind() if f["severity"] == "blocking"]
        self.assertTrue({"metronomic", "wpm"} <= {f["kind"] for f in found})
        for f in found:
            line = qa.mechanical_line(f)
            self.assertNotIn("None", line)
            self.assertIn(f["shot"], line)
        card = {"scores": {k: 6.0 for k in qa.CATEGORIES}, "overall": 6.0,
                "verdict": qa.NOT_READY, "rationale": "r", "findings": [], "blocking": [],
                "not_evidenced": [], "mechanical": found, "capped": ["editing_pacing"]}
        text = qa.findings_md("pass-01", card, [], {"regressions": [], "deltas": {}},
                              reasons=["x"])
        mechanical = text.split("## Mechanical", 1)[1].split("## Evidence sampled", 1)[0]
        self.assertNotIn("None", mechanical)
        self.assertIn("metronomic", mechanical)
        self.assertIn("editing_pacing", text)


# --------------------------------------------------------------------------- #
# the scan
# --------------------------------------------------------------------------- #

class Scan(unittest.TestCase):
    def test_scene_scores_are_read_from_ffmpegs_metadata_print(self):
        got = rhythm.parse_scene_scores(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(got, [(0.0, 0.0), (0.25, 0.001204), (0.5, 0.01293), (0.75, 0.0),
                               (1.0, 0.512877), (1.25, 0.003311), (1.5, 0.0041),
                               (1.75, 0.0)])

    def test_under_no_remote_nothing_is_launched(self):
        def never(*a, **k):
            self.fail(f"launched {a}")
        with mock.patch.dict(os.environ, {render.NO_REMOTE_ENV: "1"}), \
                mock.patch.object(rhythm.subprocess, "run", never), \
                mock.patch.object(render.subprocess, "run", never):
            with self.assertRaises(rhythm.RhythmError) as caught:
                rhythm.scan_remote(FIXTURE, None)
        self.assertIn(render.NO_REMOTE_ENV, str(caught.exception))

    def test_with_no_host_the_error_names_the_hosts_variable(self):
        hermetic.fake_remote(self)

        def never(*a, **k):
            self.fail(f"launched {a}")
        with mock.patch.object(rhythm.subprocess, "run", never), \
                mock.patch.object(render.subprocess, "run", never):
            with self.assertRaises(rhythm.RhythmError) as caught:
                rhythm.scan_remote(FIXTURE, None)
        self.assertIn("RS_RENDER_HOSTS", str(caught.exception))

    def faked(self):
        hermetic.fake_remote(self, RS_RENDER_HOSTS="render-1")
        calls, removed = [], []
        stderr = FIXTURE.read_text(encoding="utf-8")

        def run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", stderr if argv[0] == "ssh" else "")
        for patch in (mock.patch.object(rhythm.subprocess, "run", run),
                      mock.patch.object(render, "remote_host", lambda args, job: "render-1"),
                      mock.patch.object(render, "remove_remote",
                                        lambda host, path, **k: removed.append((host, path))),
                      mock.patch.dict(render._HOSTS, {"render-1": render.Host(
                          addr="render-1", ffprobe="/opt/ff/ffprobe")})):
            patch.start()
            self.addCleanup(patch.stop)
        return calls, removed

    def test_the_cut_goes_up_once_one_ssh_scans_it_and_the_directory_goes(self):
        calls, removed = self.faked()
        got = rhythm.scan_remote(FIXTURE, None, slug="film")
        self.assertEqual([c[0] for c in calls], ["rsync", "ssh"])
        script = calls[1][-1]
        self.assertIn("fps=4,scale=320:-2,select='gte(scene,0)',"
                      "metadata=print:key=lavfi.scene_score", script)
        self.assertIn("-f null -", script)
        self.assertIn("/opt/ff/ffmpeg", script)
        self.assertEqual(len(removed), 1)
        self.assertTrue(removed[0][1].startswith(f"{render.REMOTE_ROOT}/film/rhythm-"))
        self.assertEqual(len(got["scores"]), 8)
        scan = got["scan"]
        self.assertEqual((scan["host"], scan["fps"], scan["width"]), ("render-1", 4, 320))
        self.assertEqual(scan["upload"], "sent")
        self.assertEqual(scan["upload_bytes"], FIXTURE.stat().st_size)
        self.assertIn("upload_seconds", scan)

    def test_a_cut_already_on_the_host_is_not_sent_again(self):
        calls, removed = self.faked()
        got = rhythm.scan_remote(FIXTURE, None, remote_cut=("render-1", "/tmp/yt-render/f/w/cut.mp4"))
        self.assertEqual([c[0] for c in calls], ["ssh"])
        self.assertIn("/tmp/yt-render/f/w/cut.mp4", calls[0][-1])
        self.assertEqual(removed, [])
        self.assertEqual(got["scan"]["upload"], "reused")


# --------------------------------------------------------------------------- #
# reown_by_proof and cap_scores
# --------------------------------------------------------------------------- #

def finding(kind, shot, owner, severity="blocking"):
    return {"kind": kind, "shot": shot, "owner": owner, "severity": severity,
            "timestamp": 0.0, "problem": kind}


class Reown(unittest.TestCase):
    def test_a_proof_mismatch_never_reowns_a_rhythm_finding(self):
        proof = [{"kind": "proof-mismatch", "world": "w1", "shots": ["1.1", "1.2"],
                  "owner": "build"}]
        mechanical = [finding("wpm", "1.2", "voice"), finding("metronomic", "1.1", "script"),
                      finding("slow-opening", "1.1", "vfx"), finding("frozen", "1.2", "vfx")]
        moved = qa.reown_by_proof(mechanical, proof)
        self.assertEqual(moved, 1)
        self.assertEqual([f["owner"] for f in mechanical], ["voice", "script", "vfx", "build"])


class Cap(unittest.TestCase):
    def scores(self):
        return {k: 9.0 for k in qa.CATEGORIES}

    def test_a_blocking_rhythm_finding_caps_editing_pacing_only(self):
        scores, overall, capped = qa.cap_scores(self.scores(), 9.0,
                                                [finding("metronomic", "1.1", "script")])
        self.assertEqual(scores["editing_pacing"], qa.MECHANICAL_CAP)
        self.assertEqual(scores["visual_quality"], 9.0)
        self.assertEqual(capped, ["editing_pacing"])
        self.assertLess(overall, 9.0)
        self.assertEqual(qa.RHYTHM_CAPPED, ("editing_pacing",))

    def test_a_blocking_frozen_finding_still_caps_only_the_frames_categories(self):
        _, _, capped = qa.cap_scores(self.scores(), 9.0, [finding("frozen", "1.1", "vfx")])
        self.assertEqual(sorted(capped), sorted(qa.CAPPED))

    def test_a_blocking_hold_caps_both(self):
        _, _, capped = qa.cap_scores(self.scores(), 9.0, [finding("hold", "1.1", "vfx")])
        self.assertEqual(sorted(capped), sorted(qa.CAPPED + qa.RHYTHM_CAPPED))

    def test_a_minor_rhythm_finding_caps_nothing(self):
        scores, overall, capped = qa.cap_scores(
            self.scores(), 9.0, [finding(k, "1.1", "vfx", "minor") for k in qa.RHYTHM_KINDS])
        self.assertEqual((scores, overall, capped), (self.scores(), 9.0, []))

    def test_the_cap_rule_names_both_caps(self):
        self.assertIn("editing_pacing", qa.CAP_RULE)
        self.assertIn("visual_quality and animation_vfx", qa.CAP_RULE)


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #

class Record(unittest.TestCase):
    def test_the_block_holds_the_numbers_the_thresholds_and_the_gaps(self):
        build = film(LENGTHS, functions={1: "hook"})
        block = rhythm.measure(build, narrated(build), series(120.0, quiet=[(40.25, 50.0)]),
                               fps=FPS, scan={"host": "h", "fps": FPS, "width": 320,
                                              "upload_seconds": 1.5, "upload_bytes": 10})
        self.assertEqual(block["shot_lengths"], [12.0, 20.0, 30.0, 25.0, 33.0])
        self.assertEqual(block["cv"]["basis"], "events")
        self.assertEqual(block["longest_hold"]["seconds"], 10.0)
        self.assertEqual(block["longest_hold"]["shot"], "1.3")
        self.assertIsNotNone(block["opening"]["rate"])
        self.assertIsNotNone(block["opening"]["median"])
        self.assertEqual(block["edge"], ["1.1", "1.2", "1.5"])
        self.assertEqual({r["section"] for r in block["sections"]}, {"One"})
        self.assertIn("timing", block["sections"][0])
        self.assertIs(block["calibrated"], False)
        self.assertEqual(block["thresholds"]["hold_s"], 8.0)
        self.assertEqual(block["scan"]["upload_bytes"], 10)
        self.assertEqual(block["not_evidenced"], [])
        json.dumps(block)

    def test_with_no_scan_the_render_measures_are_not_evidenced(self):
        build = film(LENGTHS)
        block = rhythm.measure(build, narrated(build),
                               unscanned=("no render host configured", "yt qa"))
        named = {r["measure"] for r in block["not_evidenced"]}
        self.assertEqual(named, {"hold", "slow-opening"})
        for row in block["not_evidenced"]:
            self.assertIn("no render host configured", row["why"])
            self.assertEqual(row["command"], "yt qa")
        self.assertEqual(block["cv"]["basis"], "shots")
        self.assertIsNotNone(block["cv"]["shots"])
        self.assertIsNone(block["longest_hold"])
        self.assertTrue(block["sections"])
        self.assertEqual(kinds(block["findings"], "hold"), [])


class NoGateway(unittest.TestCase):
    def test_the_rhythm_step_asks_no_model_and_no_gateway(self):
        # The gateway switch unset: measuring and scanning reach no network
        # and no engine; the one remote step is rsync and ssh to a host.
        self.assertNotIn("YT_GATEWAY", os.environ)
        self.assertFalse({"gateway", "harness", "urllib"} & set(vars(rhythm)))
        reached = []
        hermetic.fake_remote(self, RS_RENDER_HOSTS="render-1")
        stderr = FIXTURE.read_text(encoding="utf-8")

        def run(argv, **kw):
            reached.append(argv[0])
            return subprocess.CompletedProcess(argv, 0, "", stderr)
        with mock.patch("urllib.request.urlopen", lambda *a, **k: reached.append("url")), \
                mock.patch.object(rhythm.subprocess, "run", run), \
                mock.patch.object(render, "remote_host", lambda args, job: "render-1"), \
                mock.patch.object(render, "remove_remote", lambda host, path, **k: None), \
                mock.patch.dict(render._HOSTS, {"render-1": render.Host(
                    addr="render-1", ffprobe="/opt/ff/ffprobe")}):
            got = rhythm.scan_remote(FIXTURE, None)
            build = film(LENGTHS)
            rhythm.measure(build, narrated(build), got["scores"], fps=FPS, scan=got["scan"])
        self.assertEqual(reached, ["rsync", "ssh"])


if __name__ == "__main__":
    unittest.main()
