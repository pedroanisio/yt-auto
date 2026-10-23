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
