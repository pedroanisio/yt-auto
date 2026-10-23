"""qa.sample: every shot is seen, and --frames adds evidence (AUTO-103).

The sampler used to build a fixed list of priority rows (hook, ending,
beats, section starts, 25/50/75 %, the longest effects) and `--frames`
only truncated it, so --frames 14, 20 and 40 sampled the same stills and a
section's second shot was never seen. These pin what it does now: every
shot's midpoint is a row, the priority reasons are kept as `why`, a
ceiling above the shot count adds evenly spaced stills, and one below it
keeps the priority rows and names the shots it left out.
"""

import argparse
import unittest

from ytstudio import qa
from tests.test_qa import _build


def _even(count=10, seconds=4.0, sections=("One", "Two")):
    """`count` shots of equal length, split evenly into `sections`."""
    per = count // len(sections)
    shots = [{"label": f"{n // per + 1}.{n % per + 1}", "at": n * seconds,
              "seconds": seconds, "asset": "effect",
              "section": sections[min(n // per, len(sections) - 1)]}
             for n in range(count)]
    return {"scene": {"duration": count * seconds, "fps": 30}, "shots": shots}


def _shot_of(build, row):
    return next(s for s in build["shots"] if s["label"] == row["label"])


class EveryShot(unittest.TestCase):
    def test_a_sections_second_shot_is_sampled(self):
        # 1.2 is neither a beat, a section start, a percentage mark nor one of
        # the three longest effects: before AUTO-103 nothing ever looked at it.
        build = _build()
        build["shots"][1]["asset"] = "image"
        labels = {row["label"] for row in qa.sample(build)}
        self.assertIn("1.2", labels)

    def test_by_default_every_shot_label_is_sampled_at_its_midpoint(self):
        build = _even(10)
        rows = qa.sample(build)
        self.assertEqual(sorted(r["label"] for r in rows),
                         sorted(s["label"] for s in build["shots"]))
        for row in rows:
            shot = _shot_of(build, row)
            self.assertEqual(row["t"], round(shot["at"] + shot["seconds"] / 2, 3))
        self.assertEqual(qa.unsampled(build, rows), [])

    def test_the_priority_reasons_stay_as_why_and_the_rest_say_shot(self):
        rows = {r["label"]: r["why"].split("; ") for r in qa.sample(_build())}
        self.assertIn("hook", rows["1.1"])
        self.assertIn("ending", rows["3.2"])
        self.assertIn("section start: Middle", rows["2.1"])
        self.assertIn("50% of runtime", rows["2.2"])
        self.assertTrue(all(why for why in rows.values()))

    def test_two_shots_closer_than_half_a_second_are_both_sampled(self):
        # A merge by time alone would hide one of two short shots behind the other.
        build = {"shots": [{"label": "1.1", "at": 0.0, "seconds": 0.4},
                           {"label": "1.2", "at": 0.4, "seconds": 0.4},
                           {"label": "1.3", "at": 0.8, "seconds": 3.0}]}
        self.assertEqual([r["label"] for r in qa.sample(build)], ["1.1", "1.2", "1.3"])

    def test_two_shots_with_the_same_midpoint_are_both_sampled(self):
        # p (0-2 s) and q (0.5-1.5 s) share the midpoint 1.0: one row per
        # shot, not per midpoint, so q is moved a frame inside itself.
        build = {"scene": {"fps": 30},
                 "shots": [{"label": "p", "at": 0.0, "seconds": 2.0},
                           {"label": "q", "at": 0.5, "seconds": 1.0},
                           {"label": "r", "at": 2.0, "seconds": 3.0}]}
        rows = qa.sample(build)
        self.assertEqual(sorted(r["label"] for r in rows), ["p", "q", "r"])
        self.assertEqual(qa.unsampled(build, rows), [])
        q = next(r for r in rows if r["label"] == "q")
        p = next(r for r in rows if r["label"] == "p")
        self.assertNotEqual(q["frame"], p["frame"])
        self.assertTrue(0.5 < q["t"] < 1.5, q)
        self.assertAlmostEqual(abs(q["t"] - 1.0), 1 / 30, places=3)


class Ceiling(unittest.TestCase):
    """Every shot by default, up to EVERY_SHOT_UP_TO stills: past it one
    inspection cannot read every still, so the default samples like that
    --frames and the scorecard names the shots it left out."""

    def test_a_film_within_the_ceiling_has_every_shot(self):
        build = _even(qa.EVERY_SHOT_UP_TO, seconds=2.0, sections=("One", "Two", "Three"))
        rows = qa.sample(build)
        self.assertEqual(len(rows), qa.EVERY_SHOT_UP_TO)
        self.assertEqual(qa.unsampled(build, rows), [])

    def test_a_film_past_the_ceiling_keeps_the_priority_and_names_the_rest(self):
        build = _even(150, seconds=4.0, sections=("A", "B", "C", "D", "E"))
        rows = qa.sample(build)
        self.assertEqual(len(rows), qa.EVERY_SHOT_UP_TO)
        labels = {r["label"] for r in rows}
        for label in ("1.1", "5.30", "2.1", "3.1", "4.1", "5.1"):   # hook, ending, starts
            self.assertIn(label, labels)
        missing = qa.unsampled(build, rows)
        self.assertEqual(len(missing), 150 - qa.EVERY_SHOT_UP_TO)
        # --frames still raises it
        self.assertEqual(len(qa.sample(build, limit=150)), 150)

    def test_the_note_says_the_default_ceiling_left_them_out(self):
        card = qa.note_unsampled({}, ["1.2"], total=qa.EVERY_SHOT_UP_TO + 1, limit=None)
        line = card["not_evidenced"][-1]
        self.assertIn(f"default of {qa.EVERY_SHOT_UP_TO}", line)
        self.assertIn("--frames", line)
        self.assertNotIn("reused", line)
        reused = qa.note_unsampled({}, ["1.2"], total=4, limit=None, reused=True)
        self.assertIn("reused pass", reused["not_evidenced"][-1])


class MoreFrames(unittest.TestCase):
    def test_frames_above_the_shot_count_add_evenly_spaced_stills(self):
        build = _even(10)
        rows = qa.sample(build, limit=20)
        self.assertEqual(len(rows), 20)
        extra = [r for r in rows if r["why"] == qa.SPREAD_WHY]
        self.assertEqual(len(extra), 10)
        times = sorted(r["t"] for r in rows)
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(round(b - a, 3), qa.SPREAD_GAP, times)
        # evenly over the runtime: no gap between extras wider than twice the even step
        t_extra = sorted(r["t"] for r in extra)
        step = 40.0 / 10
        self.assertLessEqual(t_extra[0], step)
        self.assertGreaterEqual(t_extra[-1], 40.0 - step)
        for a, b in zip(t_extra, t_extra[1:]):
            self.assertLessEqual(b - a, 2 * step, t_extra)

    def test_an_extra_still_belongs_to_the_shot_it_lands_in(self):
        build = _even(10)
        for row in qa.sample(build, limit=20):
            shot = _shot_of(build, row)
            self.assertLess(shot["at"], row["t"])
            self.assertLess(row["t"], shot["at"] + shot["seconds"])
            self.assertEqual(row["section"], shot["section"])

    def test_frames_14_and_40_sample_different_sets(self):
        build = _even(10)
        few = {r["t"] for r in qa.sample(build, limit=14)}
        many = {r["t"] for r in qa.sample(build, limit=40)}
        self.assertEqual(len(few), 14)
        self.assertEqual(len(many), 40)
        self.assertNotEqual(few, many)

    def test_an_extra_never_lands_in_a_gap_between_takes(self):
        build = {"shots": [{"label": "1.1", "at": 0.0, "seconds": 4.0},
                           {"label": "1.2", "at": 8.0, "seconds": 4.0}]}
        for row in qa.sample(build, limit=8):
            self.assertFalse(4.0 <= row["t"] < 8.0, row)

    def test_a_film_too_short_for_more_stills_gets_what_fits(self):
        build = {"shots": [{"label": "1.1", "at": 0.0, "seconds": 10.0}]}
        rows = qa.sample(build, limit=100)
        times = sorted(r["t"] for r in rows)
        self.assertLess(len(rows), 100)
        self.assertGreaterEqual(len(rows), 15)        # 0.25..9.75 s, half a second apart
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(round(b - a, 3), qa.SPREAD_GAP)

    def test_the_burst_stays_inside_each_shot(self):
        build = _even(10)
        fps = 30
        for row in qa.sample(build, limit=30):
            shot = _shot_of(build, row)
            first, last = row["burst"]["frames"][0], row["burst"]["frames"][-1]
            self.assertGreaterEqual(first, round(shot["at"] * fps), row)
            self.assertLess(last, round((shot["at"] + shot["seconds"]) * fps), row)
            self.assertIn(row["burst"]["still_frame"], row["burst"]["frames"])
            self.assertEqual(row["burst"]["still_frame"], round(row["t"] * fps))

    def test_a_burst_planned_from_the_shots_start_stays_after_it(self):
        # An extra still near its shot's start: the window is placed from the
        # shot's own start, not from a start guessed as t - seconds / 2.
        at, fps = 10.0, 30
        inside = qa.burst_plan(at + 0.1, fps=fps, seconds=4.0, start=at)
        self.assertGreaterEqual(inside["frames"][0], round(at * fps))
        self.assertIn(round((at + 0.1) * fps), inside["frames"])
        guessed = qa.burst_plan(at + 0.1, fps=fps, seconds=4.0)
        self.assertNotEqual(inside["frames"], guessed["frames"])

    def test_more_frames_never_give_fewer_rows(self):
        long_film = _even(150, seconds=4.0, sections=("A", "B", "C"))
        counts = [len(qa.sample(long_film, limit=n))
                  for n in (150, 400, 800, 1000, 1199, 1200, 1500, 3000)]
        self.assertEqual(counts, sorted(counts), counts)
        self.assertEqual(counts[3], 1000)
        # seven fit in a 4 s shot: its midpoint and three either side, half a
        # second apart and half a burst from its edges; 1199 asks for more
        self.assertEqual(counts[4:], [150 * 7] * 4)
        # six 2 s shots, 3 s apart: three stills fit in each, the midpoint and
        # one half a second either side of it, as far as the window allows
        gappy = {"shots": [{"label": f"1.{n + 1}", "at": n * 5.0, "seconds": 2.0}
                           for n in range(6)]}
        counts = [len(qa.sample(gappy, limit=n)) for n in range(6, 41)]
        self.assertEqual(counts, sorted(counts), counts)
        self.assertEqual(counts[-1], 18)
        self.assertEqual(len(qa.sample(gappy, limit=30)), 18)

    def test_extras_are_evenly_spaced_on_a_film_of_mixed_shots_and_gaps(self):
        lengths = (1.5, 6.0, 2.0, 9.0, 1.0, 3.0, 12.0, 2.5, 4.0, 1.2, 0.8, 5.0)
        gaps = (0.3, 0.0, 1.0, 0.35, 0.0, 2.0, 0.0, 0.5, 0.35, 0.0, 1.5)
        shots, t = [], 0.0
        for n, seconds in enumerate(lengths):
            shots.append({"label": f"1.{n + 1}", "at": t, "seconds": seconds})
            t += seconds + (gaps[n] if n < len(gaps) else 0.0)
        build = {"shots": shots}
        runtime = shots[-1]["at"] + shots[-1]["seconds"]
        for limit in (20, 30, 40):
            rows = qa.sample(build, limit=limit)
            self.assertEqual(len(rows), limit)
            extra = sorted(r["t"] for r in rows if r["why"] == qa.SPREAD_WHY)
            step = runtime / len(extra)
            # an extra cannot land in a gap between takes or within half a
            # second of another sample, so it may be pushed that far, on either
            # side, from where an even step put it; no further
            bound = step + max(gaps) + 2 * qa.SPREAD_GAP
            self.assertLessEqual(extra[0], bound, extra)
            self.assertGreaterEqual(extra[-1], runtime - bound, extra)
            for a, b in zip(extra, extra[1:]):
                self.assertLessEqual(b - a, bound, (limit, extra))


class FewerFrames(unittest.TestCase):
    def test_a_tight_limit_keeps_hook_ending_and_section_starts_and_names_the_rest(self):
        build = _build()
        rows = qa.sample(build, limit=5)
        self.assertEqual(len(rows), 5)
        labels = {r["label"] for r in rows}
        for label in ("1.1", "3.2", "2.1", "3.1"):       # hook, ending, section starts
            self.assertIn(label, labels)
        missing = qa.unsampled(build, rows)
        self.assertEqual(missing, sorted({s["label"] for s in build["shots"]} - labels))
        self.assertTrue(missing)

    def test_the_rest_spread_over_the_runtime_rather_than_from_the_start(self):
        build = _even(20, sections=("One",))
        for shot in build["shots"]:
            shot["asset"] = "image"                 # no effects: no "longest" rows
        rows = qa.sample(build, limit=6)
        # hook, ending, the 25/50/75 % shots, then the shot farthest from them
        # (1.3, at 10 s), not the next in time order (1.2)
        self.assertEqual(sorted(r["label"] for r in rows),
                         sorted(["1.1", "1.20", "1.6", "1.11", "1.16", "1.3"]))

    def test_the_note_names_how_many_shots_went_unsampled_and_which(self):
        card = qa.note_unsampled({"not_evidenced": ["x"]}, ["1.2", "2.2"], total=6,
                                 limit=4)
        self.assertEqual(card["unsampled"], ["1.2", "2.2"])
        line = card["not_evidenced"][-1]
        self.assertIn("2 of 6 shots", line)
        self.assertIn("1.2, 2.2", line)
        self.assertIn("--frames 4", line)
        self.assertEqual(card["not_evidenced"][0], "x")
        self.assertEqual(qa.note_unsampled({"not_evidenced": []}, [], total=6, limit=None),
                         {"not_evidenced": [], "unsampled": []})


class PerShotFindings(unittest.TestCase):
    def test_an_engine_diagnostic_is_filed_once_per_shot_however_many_stills_it_has(self):
        build = _even(2, seconds=8.0, sections=("One",))
        rows = qa.sample(build, limit=6)
        self.assertGreater(sum(r["label"] == "1.1" for r in rows), 1)
        found = qa.mechanical_findings(rows, diagnostics={"1.1": {"off_camera": ["a"]}})
        self.assertEqual([f["shot"] for f in found if f["kind"] == "off_camera"], ["1.1"])

    def test_it_is_filed_on_the_shots_midpoint_still(self):
        # A spread still before the midpoint comes first in time; the finding
        # still carries the shot's own moment.
        build = _even(2, seconds=8.0, sections=("One",))
        rows = qa.sample(build, limit=6)
        first = [r for r in rows if r["label"] == "1.1"]
        self.assertEqual(first[0]["why"], qa.SPREAD_WHY)
        found = [f for f in qa.mechanical_findings(rows, diagnostics={"1.1": {"off_camera": ["a"]}})
                 if f["kind"] == "off_camera"]
        self.assertEqual([f["timestamp"] for f in found], [4.0])
        self.assertEqual(found[0]["frame"], qa.frame_name(4.0))

    def test_unlabelled_shots_are_each_their_own_shot(self):
        build = {"shots": [{"label": "", "at": 0.0, "seconds": 4.0},
                           {"label": "", "at": 4.0, "seconds": 4.0}]}
        rows = qa.sample(build)
        found = [f for f in qa.mechanical_findings(rows, diagnostics={"": {"off_camera": ["a"]}})
                 if f["kind"] == "off_camera"]
        self.assertEqual([f["timestamp"] for f in found], [2.0, 6.0])


class Flag(unittest.TestCase):
    def parse(self, *argv):
        top = argparse.ArgumentParser()
        sub = top.add_subparsers()
        qa.register(sub)
        return top.parse_args(["qa", *argv])

    def test_frames_defaults_to_every_shot_and_says_so(self):
        self.assertIsNone(self.parse().frame_limit)
        self.assertEqual(self.parse("--frames", "20").frame_limit, 20)
        top = argparse.ArgumentParser()
        sub = top.add_subparsers()
        p = qa.register(sub)
        help_text = " ".join(p.format_help().split())
        self.assertIn("every shot", help_text)
        self.assertIn("evenly spaced", help_text)

    def test_frames_must_be_positive(self):
        with self.assertRaises(SystemExit):
            self.parse("--frames", "0")

    def test_the_docstring_no_longer_promises_more_evidence_from_a_cap(self):
        self.assertIn("every shot", " ".join(qa.__doc__.split()))
        self.assertIn("evenly spaced", " ".join(qa.__doc__.split()))


if __name__ == "__main__":
    unittest.main()
