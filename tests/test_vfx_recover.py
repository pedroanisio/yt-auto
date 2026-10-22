"""`yt vfx --recover`: a run that ended before it saved its record (killed by a
timeout, measured on the Euler film after 6 h) left every gated round on disk
and every review in the harness cache; the record is rebuilt from them, with
no model asked, and a section that was never reviewed is marked for
`--sections`. The model is never called and the render host is a stub."""

import unittest
from unittest.mock import patch

from ytstudio import vfx
from ytstudio.research import Dossier
from tests.test_look_section import GROUPS, OUTLINE, TWO_DOC, Case
from tests.test_vfx_world import STYLE, WORLD_DOC, FakeEngine, varied_png


class Recover(Case):
    def test_each_section_goes_back_on_its_best_reviewed_round(self):
        FakeEngine.docs = [WORLD_DOC, WORLD_DOC.replace("#AAAAAA", "#BBBBBB"), TWO_DOC, TWO_DOC]
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "first"},
                              {"frames": [], "score": 8, "summary": "better"}]

        def encode(files, doc_path, **k):
            # every round of section 2 fails its gate, so it is never reviewed
            return (["error: hero-ball is never inside the camera's view"]
                    if "02-" in str(doc_path) else [])

        planner = self.planner(revisions=1, jobs=1)
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", encode):
            planner.author(OUTLINE, GROUPS, STYLE, {"ink": "#FFFFFF"})
        # the run is killed before its record is saved
        dossier = Dossier(self.root, self.idea, at=self.root)
        dossier.drop("12-author")
        # and the active document is whatever the last round left
        doc = self.root / "vfx" / "v01" / "01-open.scene.xml"
        doc.write_text(WORLD_DOC.replace("#AAAAAA", "#CCCCCC"))
        FakeEngine.stream_calls = []

        rebuilt = {r["n"]: r for r in self.planner(recover=True).recovered(OUTLINE, GROUPS)}
        self.assertEqual(FakeEngine.stream_calls, [])                 # no model asked
        one, two = rebuilt[1], rebuilt[2]
        self.assertEqual((one["best_round"], one["score"], one["version"]), (1, 8.0, "v01"))
        self.assertIn("#BBBBBB", doc.read_text())                    # its best round is back
        # AUTO-82: the record the section wrote is read back, its round's own
        # findings (warnings) with it; none is an error
        self.assertFalse([f for f in one["findings"] if f.startswith("error")])
        self.assertTrue(one["recovered"])
        self.assertGreater(one["cost_usd"], 0)                       # the reviews were paid for
        self.assertTrue(one["symbols"])
        self.assertIsNone(two["score"])
        self.assertTrue(two["findings"][0].startswith("error: recovered"))
        self.assertIn("--sections 2", two["findings"][0])

    def test_take_attempt_puts_back_a_new_attempt_the_competition_beat(self):
        # measured on the Euler film: the re-authored section 7 (5.5) lost to
        # the version it replaced (6.0), which no 8 GB GPU could encode
        FakeEngine.docs = [WORLD_DOC, TWO_DOC]
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "heavy"},
                              {"frames": [], "score": 8, "summary": "two"}]
        self.author(self.planner(revisions=0, jobs=1))
        FakeEngine.docs = [WORLD_DOC.replace("#AAAAAA", "#BBBBBB")]
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "light"}]
        kept = {r["n"]: r for r in self.author(self.planner(revisions=0, jobs=1,
                                                            sections_only={1}))}
        self.assertEqual(kept[1]["kept_from"], "v01")
        FakeEngine.stream_calls = []
        planner = self.planner(take_attempt={1}, take_why="v01's section 1 cannot encode")
        taken = {r["n"]: r for r in planner.taken(OUTLINE, GROUPS)}
        self.assertEqual(FakeEngine.stream_calls, [])
        self.assertEqual(taken[1]["score"], 5.0)
        self.assertEqual(taken[1]["taken"]["over"], "v01")
        self.assertIn("cannot encode", taken[1]["taken"]["why"])
        self.assertIn("#BBBBBB", (self.root / taken[1]["file"]).read_text())
        self.assertEqual(taken[2], kept[2])                       # the others untouched
        with self.assertRaises(ValueError):                       # nothing left to take
            self.planner(take_attempt={2}).taken(OUTLINE, GROUPS)

    def test_recover_needs_an_authored_version(self):
        with self.assertRaises(ValueError):
            self.planner(recover=True).recovered(OUTLINE, GROUPS)

    def test_the_cli_refuses_recover_with_other_modes(self):
        parser = vfx.argparse.ArgumentParser()
        sub = parser.add_subparsers()
        vfx.register(sub)
        args = parser.parse_args(["vfx", "--recover", "--fresh"])
        self.assertTrue(args.recover and args.fresh)


if __name__ == "__main__":
    unittest.main()
