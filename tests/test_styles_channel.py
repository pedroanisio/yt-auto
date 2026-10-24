"""AUTO-137: `yt styles` warns when its final primary row reuses a counted
earlier film's treatment, or its parsed background and accent.

The warning is measured after the critique has had its say on the order, so
it follows the row that is actually primary. It changes no pick and no
prompt: the critique is the author's own family, and channel facts never
reach a judgement that family answers. The model is never called: the
Director's `_ask` is a fake.
"""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytstudio import channel, cli, styles
from ytstudio.core import Idea

NAVY_GOLD = "background (#14213D), accent (#F5B700), text (#F1E9D2)"
GREEN_RED = "background (#0B6E4F), accent (#D7263D), text (#FFFFFF)"

#: The pick before the critique: Diagram first, Flat second.
CHOICE = {
    "message": "m", "demands": ["d"], "combination": "", "avoid": [],
    "styles": [
        {"name": "Diagram", "fit": 9, "why": "w", "shows_well": "s", "shots": [],
         "effort": "low", "pitfalls": "", "palette": GREEN_RED, "typography": "Inter"},
        {"name": "Flat", "fit": 7, "why": "w", "shows_well": "s", "shots": [],
         "effort": "low", "pitfalls": "", "palette": NAVY_GOLD, "typography": "Inter"},
    ],
}

#: A critique that demotes the first pick below the second.
DEMOTE = {"verdicts": [{"name": "Diagram", "attack": "a", "holds": False, "fit": 4},
                       {"name": "Flat", "attack": "a", "holds": True, "fit": 7}],
          "overlooked": []}
KEEP = {"verdicts": [], "overlooked": []}

#: A counted earlier film that used Flat in navy and gold.
EARLIER = [{"slug": "old-bridge", "counted": True, "treatment": "Flat",
            "palette": {"background": "#14213D", "accent": "#F5B700"},
            "opening": "", "closing": "", "shape": None}]


def faked(critique=DEMOTE):
    asked = []

    def ask(self_, name, prompt, schema):
        asked.append({"name": name, "prompt": prompt, "schema": schema})
        if schema is styles.CRITIQUE_SCHEMA:
            return copy.deepcopy(critique)
        return copy.deepcopy(CHOICE)
    return asked, ask


class Director(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.idea = Idea(title="Why the bridge fell", genre="explainer", description="d")

    def run_director(self, earlier=(), critique=DEMOTE):
        asked, ask = faked(critique)
        director = styles.Director(self.idea, root=self.root, verbose=False, count=2,
                                   rich=True, modes={"critique"}, earlier=earlier)
        with mock.patch.object(styles.Director, "_ask", ask):
            data, picks = director.run([("Diagram", "d"), ("Flat", "f"), ("Chalk", "c")])
        return asked, data, picks, director

    def test_with_earlier_films_every_prompt_is_unchanged(self):
        before, *_ = self.run_director()
        after, *_ = self.run_director(earlier=EARLIER)
        self.assertEqual([(a["name"], a["prompt"]) for a in after],
                         [(b["name"], b["prompt"]) for b in before])
        self.assertEqual(len(after), 2)
        for a in after:
            self.assertNotIn("old-bridge", a["prompt"])

    def test_the_role_is_unchanged(self):
        *_, plain = self.run_director()
        *_, listed = self.run_director(earlier=EARLIER)
        self.assertEqual(listed.harness.role, plain.harness.role)

    def test_the_repeat_follows_the_primary_row_after_the_critique(self):
        _, data, picks, _ = self.run_director(earlier=EARLIER)
        self.assertEqual([p.name for p in picks], ["Flat", "Diagram"])
        found = data["channel"]["repeats"]
        self.assertEqual(data["channel"]["films"], 1)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[0].startswith("channel: treatment: Flat"), found)
        self.assertTrue(found[1].startswith("channel: palette:"), found)
        self.assertIn("old-bridge", found[1])

    def test_without_the_reorder_the_first_pick_is_measured(self):
        _, data, picks, _ = self.run_director(earlier=EARLIER, critique=KEEP)
        self.assertEqual(picks[0].name, "Diagram")
        self.assertEqual(data["channel"], {"repeats": [], "films": 1})

    def test_the_pick_is_the_same_with_or_without_the_channel(self):
        _, _, plain, _ = self.run_director()
        _, _, listed, _ = self.run_director(earlier=EARLIER)
        self.assertEqual([(p.name, p.fit) for p in listed], [(p.name, p.fit) for p in plain])

    def test_with_none_there_is_no_channel_key(self):
        _, data, _, _ = self.run_director()
        self.assertNotIn("channel", data)


class Command(unittest.TestCase):
    """`yt styles` reads the channel from the work root, warns, saves the
    warning to styles.json and records the film."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / "work"
        self.film = self.work / "new-bridge"
        self.film.mkdir(parents=True)
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def earlier_film(self):
        old = self.work / "old-bridge"
        old.mkdir()
        (old / "styles.json").write_text(json.dumps(
            {"styles": [{"name": "Flat", "palette": NAVY_GOLD}]}))
        (old / "qa.json").write_text(json.dumps({"verdict": channel.READY}))

    def run_stage(self, *flags):
        asked, ask = faked()
        args = cli.build_parser().parse_args(
            ["styles", "--project", str(self.film), "--work", str(self.work), "--quiet",
             "--rich", "--deliberate", "critique", "--count", "2", *flags])
        with mock.patch.object(styles.Director, "_ask", ask), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = styles.run(args)
        return code, out.getvalue() + err.getvalue()

    def test_a_repeat_is_warned_saved_and_the_film_recorded(self):
        self.earlier_film()
        code, said = self.run_stage()
        self.assertEqual(code, 0, said)
        self.assertIn("warning: channel: treatment: Flat", said)
        self.assertIn("warning: channel: palette:", said)
        saved = json.loads((self.film / "styles.json").read_text())
        self.assertEqual(saved["styles"][0]["name"], "Flat")
        self.assertEqual(len(saved["channel"]["repeats"]), 2)
        rows = json.loads((self.work / "channel.json").read_text())["films"]
        self.assertEqual({r["slug"]: r["counted"] for r in rows},
                         {"old-bridge": True, "new-bridge": False})
        mine = next(r for r in rows if r["slug"] == "new-bridge")
        self.assertEqual(mine["treatment"], "Flat")

    def test_a_first_film_saves_no_channel_and_warns_nothing(self):
        code, said = self.run_stage()
        self.assertEqual(code, 0, said)
        self.assertNotIn("channel:", said)
        saved = json.loads((self.film / "styles.json").read_text())
        self.assertNotIn("channel", saved)

    def test_an_uncounted_earlier_film_is_not_compared(self):
        self.earlier_film()
        (self.work / "old-bridge" / "qa.json").write_text(json.dumps({"verdict": "NOT READY"}))
        code, said = self.run_stage()
        self.assertEqual(code, 0, said)
        self.assertNotIn("channel", json.loads((self.film / "styles.json").read_text()))


if __name__ == "__main__":
    unittest.main()
