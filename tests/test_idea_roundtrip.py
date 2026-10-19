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

    def test_a_title_is_still_required(self):
        with self.assertRaises(TypeError):
            Idea.from_raw({"genre": "g"})


if __name__ == "__main__":
    unittest.main()
