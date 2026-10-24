"""`yt vfx --recover`: a run that ended before it saved its record (killed by a
timeout, measured on the Euler film after 6 h) left every gated round on disk
and every review in the harness cache; the record is rebuilt from them, with
no model asked, and a section that was never reviewed is marked for
`--sections`. The model is never called and the render host is a stub."""

import unittest
from unittest.mock import patch

from ytstudio import harness, vfx
from ytstudio.core import write_json
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
        self.assertEqual((taken[1]["author"], taken[1]["bar"]), ("sonnet", 7.0))
        self.assertIn("cannot encode", taken[1]["taken"]["why"])
        self.assertIn("#BBBBBB", (self.root / taken[1]["file"]).read_text())
        self.assertEqual(taken[2], kept[2])                       # the others untouched
        with self.assertRaises(ValueError):                       # nothing left to take
            self.planner(take_attempt={2}).taken(OUTLINE, GROUPS)

    def keep(self, ws, name: str, round_no: int, doc: str, review: dict | None) -> None:
        """A gated round as a run leaves it: kept under rounds/<stem>/rNN with
        its review in the harness cache (None: the round went unreviewed)."""
        (ws.dir / name).write_text(doc, encoding="utf-8")
        ws.keep_round(name, round_no)
        if review is not None:
            cache = self.root / harness.DECISIONS_DIR / "vfx"
            cache.mkdir(parents=True, exist_ok=True)
            write_json(cache / f"review-{ws.version}-{int(name[:2]):02d}-r{round_no}.json",
                       {"result": review, "cost_usd": 0.5})

    def test_the_reconstruction_follows_the_verdicts_and_a_tie_keeps_the_earlier_round(self):
        # no record was written (a run from before AUTO-82): the rounds and the
        # cached reviews decide, by the same rule the run promotes with
        ws = vfx.Workspace(self.root, version="v01")
        worse = {"frames": [], "score": 6, "summary": "higher but worse",
                 "previous": {"verdict": "worse", "findings": []}}
        self.keep(ws, "01-open.scene.xml", 0, WORLD_DOC, {"frames": [], "score": 5, "summary": "a"})
        self.keep(ws, "01-open.scene.xml", 1, WORLD_DOC.replace("#AAAAAA", "#BBBBBB"), worse)
        self.keep(ws, "02-two.scene.xml", 0, TWO_DOC, {"frames": [], "score": 7, "summary": "b"})
        self.keep(ws, "02-two.scene.xml", 1, TWO_DOC.replace("#AAAAAA", "#BBBBBB"),
                  {"frames": [], "score": 7, "summary": "c"})
        rebuilt = {r["n"]: r for r in self.planner(recover=True).recovered(OUTLINE, GROUPS)}
        self.assertEqual((rebuilt[1]["best_round"], rebuilt[1]["score"]), (0, 5))
        self.assertIn("#AAAAAA", (self.root / rebuilt[1]["file"]).read_text())
        self.assertEqual((rebuilt[2]["best_round"], rebuilt[2]["score"]), (0, 7))
        self.assertIn("#AAAAAA", (self.root / rebuilt[2]["file"]).read_text())

    def test_a_section_rebuilt_from_rounds_names_its_author_and_bar(self):
        # no record.json for this version: the rounds were authored by this
        # version's run, so the rebuilt record names its model and bar, and a
        # reviewer of another family is still judged apart from it (AUTO-153)
        ws = vfx.Workspace(self.root, version="v01")
        self.keep(ws, "01-open.scene.xml", 0, WORLD_DOC, {"frames": [], "score": 5, "summary": "a"})
        self.keep(ws, "02-two.scene.xml", 0, TWO_DOC, {"frames": [], "score": 7, "summary": "b"})
        planner = self.planner(recover=True, reviewer_model="opus")
        rebuilt = planner.recovered(OUTLINE, GROUPS)
        self.assertEqual([(r["author"], r["bar"]) for r in rebuilt],
                         [("sonnet", 7.0), ("sonnet", 7.0)])
        self.assertTrue(vfx.judged_apart(planner.continuity(rebuilt)))

    def test_a_partial_record_folds_in_a_reviewed_round_kept_after_it(self):
        # killed between keep_round(1) and the record's write: the round the
        # record does not know is on disk with its review, and it is promoted
        ws = vfx.Workspace(self.root, version="v01")
        name = "01-open.scene.xml"
        first = {"frames": [], "score": 5, "summary": "a"}
        self.keep(ws, name, 0, WORLD_DOC, first)
        ws.write_record(name, {
            "n": 1, "section": "Open", "file": f"vfx/v01/{name}", "version": "v01",
            "finished": False, "symbols": ["world-open"], "findings": [], "revisions": 0,
            "rounds": [{"round": 0, "score": 5, "errors": 0, "review": first, "timed_out": False,
                        "symbols": ["world-open"], "findings": []}],
            "best_round": 0, "score": 5, "review": first, "cost_usd": 0.5})
        self.keep(ws, name, 1, WORLD_DOC.replace("#AAAAAA", "#BBBBBB"),
                  {"frames": [], "score": 8, "summary": "b",
                   "previous": {"verdict": "better", "findings": []}})
        self.keep(ws, "02-two.scene.xml", 0, TWO_DOC, {"frames": [], "score": 7, "summary": "c"})
        rebuilt = {r["n"]: r for r in self.planner(recover=True).recovered(OUTLINE, GROUPS)}
        one = rebuilt[1]
        self.assertEqual([r["round"] for r in one["rounds"]], [0, 1])
        self.assertEqual((one["best_round"], one["score"]), (1, 8))
        self.assertIn("#BBBBBB", (self.root / one["file"]).read_text())
        self.assertFalse([f for f in one["findings"] if f.startswith("error")])

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
