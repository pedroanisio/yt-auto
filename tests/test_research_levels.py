"""Research decides in dependency levels, not in series.

Claims, sources and the shot list need only the notes; the asset search needs
the shots and the sources; the gap audit needs the claims and the sources; the
outline needs all three. Run in series on opus those six decisions were most
of an hour. They now run on one pool, `--jobs` wide, level by level. The stage
names are unchanged, so a dossier researched in series replays, and the
dossier's writer is safe with the levels saving beside each other.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import assets as yt_assets  # noqa: E402
from ytstudio import research  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

NOTES = [{"question": "q", "notes": "n", "visited": []}]

FIRST = ("03-claims", "04-sources", "05-shots")
SECOND = ("05-assets", "06-gaps")

#: What a finished research leaves under raw/, whichever way it was run.
RAW = ["01-questions", "02-research", "03-claims", "04-sources", "05-assets",
       "05-shots", "06-gaps", "07-outline", "07-outline-assets"]


class RecordingHarness:
    """Answers every decision with an empty form and records when each one
    starts and ends. The first-level decisions wait for each other at a
    barrier, so a run that makes them in series breaks it instead of hanging."""

    def __init__(self, parties: int) -> None:
        self.events: list[tuple[str, str]] = []
        self.usage = Usage()
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(parties, timeout=5)

    def note(self, what: str, name: str) -> None:
        with self._lock:
            self.events.append((what, name))

    def decide(self, name, prompt, schema):
        self.note("start", name)
        if name in FIRST:
            self._barrier.wait()
        self.note("end", name)
        return {key: [] for key in schema.get("required", [])}

    def report(self) -> dict:
        return {}

    def starts(self, names) -> list[int]:
        return [n for n, (what, name) in enumerate(self.events)
                if what == "start" and name in names]


class Levels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.idea = Idea(title="Comets", genre="explainer", description="What a tail is.")
        # The catalogue search is the asset stage's own work, recorded like a
        # decision; nothing here reaches the network.
        self.searches: list[list] = []
        for name, side in (("find_assets", self._search), ("cited_papers", lambda *a, **k: [])):
            p = mock.patch.object(yt_assets, name, side_effect=side)
            p.start()
            self.addCleanup(p.stop)

    def _search(self, shots, **kw):
        self.harness.note("start", "05-assets")
        self.searches.append(shots)
        return []

    def researcher(self, *, jobs: int, parties: int | None = None):
        dossier = research.Dossier(self.root, self.idea, at=self.root)
        r = research.Researcher(self.idea, dossier, verbose=False, jobs=jobs)
        self.harness = r.harness = RecordingHarness(parties or jobs)
        return r

    def raw(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "raw").iterdir())

    def test_the_first_level_starts_before_any_second_level_decision(self):
        data = self.researcher(jobs=3).run()
        first, second = self.harness.starts(FIRST), self.harness.starts(SECOND)
        self.assertEqual(len(first), 3, self.harness.events)
        self.assertEqual(len(second), 2, self.harness.events)
        self.assertLess(max(first), min(second), self.harness.events)
        outline = self.harness.starts(("07-outline",))
        self.assertGreater(min(outline), max(second), self.harness.events)
        self.assertEqual(self.raw(), RAW)
        self.assertEqual(data["scope"], "explainer")

    def test_the_second_level_waits_for_the_first_to_end(self):
        self.researcher(jobs=3).run()
        ends = [n for n, (what, name) in enumerate(self.harness.events)
                if what == "end" and name in FIRST]
        self.assertLess(max(ends), min(self.harness.starts(SECOND)))

    def test_the_dossier_replays_whichever_way_it_was_made(self):
        serial = self.researcher(jobs=1, parties=1).run()
        made = self.raw()
        again = self.researcher(jobs=3).run()
        self.assertEqual(self.harness.events, [])            # every stage cached
        self.assertEqual(self.raw(), made)
        self.assertEqual(made, RAW)
        self.assertEqual(again, serial)

    def test_the_asset_search_takes_the_shot_plan_it_is_given(self):
        r = self.researcher(jobs=1, parties=1)
        plan = {"shots": [{"shot": "a tail"}], "topic_searches": ["comet"]}
        r.assets(NOTES, {"sources": []}, plan=plan)
        self.assertEqual(self.searches, [plan["shots"]])
        self.assertEqual(self.harness.starts(("05-shots",)), [])
        self.assertNotIn("05-shots", self.raw())

    def test_the_jobs_flag_names_the_decisions(self):
        p = research.register(argparse.ArgumentParser().add_subparsers())
        text = p.format_help().rsplit("--jobs", 1)[1].split("  --", 1)[0]
        self.assertIn("decisions", text)


class Writer(unittest.TestCase):
    """Dossier.save lands a whole file or none: a reader never meets a stage
    half written by another thread."""

    def test_a_reader_sees_a_whole_stage_or_the_old_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            idea = Idea(title="Comets", genre="explainer", description="d")
            dossier = research.Dossier(Path(tmp), idea, at=Path(tmp))
            payload = {"rows": ["x" * 200] * 5000}
            dossier.save("03-claims", payload)
            torn = []
            stop = threading.Event()

            def write():
                for _ in range(40):
                    dossier.save("03-claims", payload)
                stop.set()

            def read():
                while not stop.is_set():
                    if dossier.load("03-claims") != payload:
                        torn.append(1)

            threads = [threading.Thread(target=write)] + \
                [threading.Thread(target=read) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(torn, [])
            self.assertEqual(sorted(p.name for p in (Path(tmp) / "raw").iterdir()),
                             ["03-claims.json"])


if __name__ == "__main__":
    unittest.main()
