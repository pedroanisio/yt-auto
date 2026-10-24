"""voice.summarise carries each section's words a minute (AUTO-141), so `yt
voice` and `yt doctor` show the read's rate before a render is paid for,
computed by the same rhythm.section_wpm `yt qa` files its findings from."""

import contextlib
import io
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from ytstudio import doctor, rhythm, voice, workspace
from ytstudio.core import write_json


def take(n, label, section, words, seconds, timing="exact", gap_after=None, gap=0.0):
    rows, t = [], 0.0
    for i in range(words):
        rows.append(voice.Word(f"w{i}", round(t, 3), round(t + 0.3, 3)))
        t += 0.3
        if gap_after == i:
            t += gap
    return voice.Take(n=n, label=label, section=section, text=" ".join(w.text for w in rows),
                      planned_seconds=int(seconds), seconds=seconds, timing=timing,
                      words=rows if timing != "none" else [])


class BySection(unittest.TestCase):
    def test_each_section_row_carries_wpm_and_timing(self):
        takes = [take(1, "1.1", "One", 20, 6.0),
                 take(2, "1.2", "Two", 20, 7.4, gap_after=9, gap=1.4),
                 take(3, "2.1", "Three", 20, 8.0, timing="estimated")]
        rows = voice.summarise(takes, layout="sequential")["by_section"]
        self.assertAlmostEqual(rows["One"]["wpm"], 200.0, delta=0.5)
        self.assertEqual(rows["One"]["timing"], "exact")
        self.assertAlmostEqual(rows["Two"]["wpm"], 200.0, delta=0.5)   # the pause left out
        self.assertEqual(rows["Three"]["timing"], "estimated")
        self.assertAlmostEqual(rows["Three"]["wpm"], 150.0, delta=0.5)
        # the same function QA reads, so the two numbers cannot drift
        same = {r["section"]: r["wpm"] for r in rhythm.section_wpm(
            [asdict(t) for t in takes])}
        self.assertEqual(same, {k: v["wpm"] for k, v in rows.items()})
        for row in rows.values():               # what was there before is kept
            for key in ("planned", "spoken", "takes", "drift"):
                self.assertIn(key, row)

    def test_the_directors_brief_is_the_drift_it_was_before(self):
        rows = voice.summarise([take(1, "1.1", "One", 20, 6.0)], layout="sequential")
        self.assertEqual(voice.drift_by_section(rows["by_section"]),
                         {"One": {"planned": 6, "spoken": 6.0, "takes": 1, "drift": 0.0}})


class PrintVoice(unittest.TestCase):
    def test_yt_voice_shows_each_sections_wpm_on_its_header(self):
        takes = [take(1, "1.1", "One", 20, 6.0),
                 take(2, "2.1", "Three", 20, 8.0, timing="estimated")]
        data = voice.summarise(takes, layout="sequential")
        data.update(idea={"title": "T"}, engine="kokoro", voice="af", timing=["exact"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            voice.print_voice(data, takes)
        heads = [line.strip() for line in out.getvalue().splitlines()
                 if line.strip().startswith(("One", "Three"))]
        self.assertEqual(len(heads), 2, out.getvalue())
        self.assertIn("200 wpm (exact)", heads[0])
        self.assertIn("150 wpm (estimated)", heads[1])


class Doctor(unittest.TestCase):
    def test_doctor_names_wpm_per_section_before_any_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "idea.json", {"title": "T", "genre": "g", "description": "d"})
            takes = [take(1, "1.1", "One", 20, 6.0),
                     take(3, "2.1", "Three", 20, 8.0, timing="estimated")]
            data = voice.summarise(takes, layout="sequential")
            write_json(root / "voice.json", dict(data, engine="kokoro",
                                                 takes=[asdict(t) for t in takes]))
            project = workspace.Project(root)
            self.assertFalse(project.has("video"))
            lines = [f.message for f in doctor.check_project(project).findings
                     if f.area == "voice" and "wpm" in f.message]
        self.assertEqual(lines, ["wpm by section: One 200 · Three 150 (estimated)"])


if __name__ == "__main__":
    unittest.main()
