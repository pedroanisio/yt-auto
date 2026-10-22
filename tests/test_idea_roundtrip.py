"""An idea the pipeline saves, it can read back.

`compact()` drops empty fields when an idea is written, so a project made
from a bare `--project DIR` saves only its title; `Idea.from_raw` must read
that back instead of failing every later stage with a TypeError (found when
`yt render --project` ran twice on a scratch directory).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import workspace  # noqa: E402
from ytstudio.core import Idea, compact  # noqa: E402


class RoundTrip(unittest.TestCase):
    def test_an_idea_with_empty_fields_survives_compact(self):
        idea = Idea(title="shaderproof", genre="", description="")
        self.assertEqual(Idea.from_raw(compact(idea)), idea)

    def test_a_title_only_idea_json_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scratch"
            root.mkdir()
            (root / "idea.json").write_text(json.dumps({"title": "scratch"}), encoding="utf-8")
            idea = workspace.Project(root).load_idea()
            self.assertEqual((idea.title, idea.genre, idea.description), ("scratch", "", ""))

    def test_an_older_ideas_json_without_the_plan_fields_loads(self):
        # AUTO-100 added question, stakes, viewer_belief and promise (with
        # `--rich`) and the rank labels; an ideas.json written before has none
        # of them and must still read, with each empty.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / workspace.IDEAS_FILE).write_text(json.dumps([
                {"title": "Old idea", "genre": "explainer", "description": "d",
                 "hook": "h", "score": 7, "why": "w"}]), encoding="utf-8")
            rows = json.loads((work / workspace.IDEAS_FILE).read_text(encoding="utf-8"))
            idea = Idea.from_raw(rows[0])
        self.assertEqual((idea.question, idea.stakes, idea.viewer_belief, idea.promise),
                         ("", "", "", ""))
        self.assertEqual((idea.curiosity, idea.title_concrete), ("", ""))
        self.assertEqual((idea.title, idea.score), ("Old idea", 7))

    def test_the_plan_fields_survive_compact(self):
        idea = Idea(title="t", genre="g", description="d", question="q?",
                    stakes="s", viewer_belief="b", promise="p",
                    curiosity="brief", title_concrete="concrete")
        self.assertEqual(Idea.from_raw(compact(idea)), idea)

    def test_a_title_is_still_required(self):
        with self.assertRaises(TypeError):
            Idea.from_raw({"genre": "g"})


if __name__ == "__main__":
    unittest.main()
