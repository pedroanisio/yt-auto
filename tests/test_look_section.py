"""The look comes from the film itself: `yt keyframe` authors the lead
section once through `yt vfx`'s planner, judged by the mechanical gate only;
the operator approves its frames; `yt vfx` carries it forward and authors the
other sections against its program. `yt run` stops before vfx while the look
awaits approval. The model is never called (`harness.ENGINE` is a fake) and
the render host is a stub.
"""

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ytstudio import cli, doctor, harness, keyframe, vfx, workspace
from ytstudio.core import Idea
from ytstudio.research import Dossier
from tests.test_keyframe import STYLES
from tests.test_vfx_world import EFFECTS, SECTION, STYLE, WORLD_DOC, FakeEngine, varied_png

TWO = {"heading": "Two", "seconds": 15, "beats": ["b"], "shots": [
    dict(s, label="2." + s["label"].split(".")[1], at=s["at"] + 15) for s in SECTION["shots"]]}
TWO_EFFECTS = [dict(e, id=e["id"].replace("open", "two"), section="Two",
                    shots=["2." + s.split(".")[1] for s in e["shots"]]) for e in EFFECTS]
OUTLINE = {"sections": [SECTION, TWO]}
GROUPS = [{"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []},
          {"section": "Two", "seconds": 15, "effects": TWO_EFFECTS, "cuts": []}]
TWO_DOC = WORLD_DOC.replace("world-open", "world-two")


def png(shade: int, *, flat: bool = False) -> bytes:
    im = Image.new("RGB", (64, 36), (shade, shade, shade))
    if not flat:
        for x in range(0, 64, 2):
            for y in range(36):
                im.putpixel((x, y), (255 - shade, 30, 200))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc, FakeEngine.docs = WORLD_DOC, []
        FakeEngine.time_out_on = FakeEngine.crash_on = 0
        self.idea = Idea(title="T", genre="g", description="d")
        # a palette of the film's own: a look authored on design.py's house
        # fallback cannot be approved (AUTO-155)
        (self.root / "styles.json").write_text(json.dumps(STYLES))

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def planner(self, **kw) -> vfx.VfxPlanner:
        bundle = {"idea": self.idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        kw.setdefault("revisions", 2)
        return vfx.VfxPlanner(bundle, Dossier(self.root, self.idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", sections=[SECTION, TWO],
                              author_bar=7.0, verbose=False, **kw)

    def author(self, planner):
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return planner.author(OUTLINE, GROUPS, STYLE, {"ink": "#FFFFFF"})

    def authors(self):
        return [c for c in FakeEngine.stream_calls if "Write" in c["tools"]]

    def reviews(self):
        return [c for c in FakeEngine.stream_calls if "Write" not in c["tools"]]

    def look_record(self, lead: dict, state: str = "pending") -> dict:
        """keyframe.json as SectionLook writes it, for the lead record."""
        doc = self.root / lead["file"]
        record = {"source": "section", "section": lead["n"], "asked_section": lead["n"],
                  "heading": lead["section"], "version": lead["version"],
                  "document": lead["file"], "program": lead["program"],
                  "frame": "keyframe/keyframe.png", "sheet": "keyframe/frames.png",
                  "frames": [], "record": lead, "findings": [], "errors": [], "rounds": [],
                  "score": None, "elements": [], "operator_notes": [],
                  "document_digest": keyframe.document_digest(doc),
                  "program_digest": keyframe.document_digest(self.root / lead["program"]),
                  "approval": {"state": "pending", "notes": "",
                               "digest": keyframe.document_digest(doc), "at": "2026-10-20T19:00:00"}}
        (self.root / "keyframe").mkdir(exist_ok=True)
        (self.root / "keyframe" / "look.py").write_bytes((self.root / lead["program"]).read_bytes())
        record["look_program"] = "keyframe/look.py"
        record["frame_size"] = [1920, 1080]
        (self.root / "keyframe" / "keyframe.png").write_bytes(png(40))
        (self.root / "keyframe" / "frames.png").write_bytes(png(40))
        (self.root / "keyframe.json").write_text(json.dumps(record))
        if state == "approved":
            keyframe.approve(self.root)
        elif state == "rejected":
            keyframe.reject(self.root, "the labels are too small")
        return keyframe.load_record(self.root)


class LeadPass(Case):
    """The look pass authors one section, once, and pays for no review."""

    def test_only_the_lead_section_is_authored_once_with_no_review(self):
        FakeEngine.reviews = [{"frames": [], "score": 3, "summary": "would send it back"}]
        authored = self.author(self.planner(lead=1))
        self.assertEqual([r["n"] for r in authored], [1])
        lead = authored[0]
        self.assertEqual(lead["version"], "v01")
        self.assertEqual(lead["revisions"], 0)
        self.assertIsNone(lead["score"])
        self.assertEqual(len(self.authors()), 1)
        self.assertEqual(self.reviews(), [])               # the gate alone judges it
        self.assertEqual(len(FakeEngine.reviews), 1)       # the scripted review went unread
        self.assertTrue((self.root / lead["program"]).is_file())
        # kept apart from the film's authoring record, which it must not stand for
        dossier = Dossier(self.root, self.idea, at=self.root)
        self.assertEqual([r["n"] for r in dossier.load("12-lead")], [1])
        self.assertIsNone(dossier.load("12-author"))

    def test_a_lead_section_with_no_planned_effects_falls_to_the_first_that_has_some(self):
        groups = [dict(GROUPS[0], effects=[]), GROUPS[1]]
        FakeEngine.docs = [TWO_DOC]
        planner = self.planner(lead=1)
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            authored = planner.author(OUTLINE, groups, STYLE, {"ink": "#FFFFFF"})
        self.assertEqual([r["n"] for r in authored], [2])
        self.assertEqual(planner.lead, 2)

    def test_the_operators_notes_lead_the_brief_and_no_earlier_look_is_inherited(self):
        from tests.test_vfx_world import approve_keyframe
        approve_keyframe(self.root)                       # an older approved look on disk
        self.author(self.planner(lead=1, look_notes=["the labels are too small"]))
        brief = (self.root / "vfx" / "v01" / "brief-01.md").read_text()
        self.assertTrue(brief.startswith("## The operator's notes on the last look"))
        self.assertIn("- the labels are too small", brief)
        self.assertNotIn("## The approved look", brief)

    def test_the_look_pass_builds_on_no_look_so_its_workspace_has_no_module(self):
        # A look pass after an approval (`--section 2`, `--fresh`) makes the next
        # look; the old one is not `look.py` in its workspace. Were it there, a
        # lead program importing it would be kept as keyframe/look.py and, once
        # approved, install as a look.py that imports `look`: every build of the
        # next version fails on it.
        lead = self.author(self.planner(lead=1))[0]
        self.look_record(lead, "approved")
        FakeEngine.stream_calls, FakeEngine.docs = [], [TWO_DOC]
        self.author(self.planner(lead=2))
        self.assertTrue((self.root / "vfx" / "v02" / "srkit.py").is_file())
        self.assertFalse((self.root / "vfx" / "v02" / "look.py").exists())
        FakeEngine.stream_calls, FakeEngine.docs = [], [WORLD_DOC]
        self.author(self.planner(lead=1, fresh=True))
        self.assertFalse((self.root / "vfx" / "v03" / "look.py").exists())
        # keyframe/look.py stays the approved bytes until the new look is approved
        self.assertEqual((self.root / "keyframe" / "look.py").read_bytes(),
                         (self.root / lead["program"]).read_bytes())


class CarryTheLook(Case):
    """Once approved, `yt vfx` carries the lead section and authors the rest against it."""

    def lead(self) -> dict:
        lead = self.author(self.planner(lead=1))[0]
        FakeEngine.stream_calls = []
        return lead

    def test_the_approved_section_is_carried_and_only_the_others_are_authored(self):
        lead = self.lead()
        self.look_record(lead, "approved")
        FakeEngine.docs = [TWO_DOC]
        FakeEngine.reviews = [{"frames": [], "score": 8, "fidelity": 8, "summary": "same film"}]
        authored = self.author(self.planner())
        self.assertEqual([r["n"] for r in authored], [1, 2])
        carried, other = authored
        self.assertEqual(carried["version"], "v02")
        self.assertEqual(carried["carried_from"], "v01")
        self.assertEqual(carried["cost_usd"], 0.0)
        self.assertTrue((self.root / carried["file"]).is_file())
        self.assertEqual(len(self.authors()), 1)
        self.assertIn("02-", self.authors()[0]["prompt"])
        brief = (self.root / "vfx" / "v02" / "brief-02.md").read_text()
        self.assertIn("## The approved look (section 1 of this film", brief)
        # the approved bytes, where the author may read: never another version
        self.assertIn("Read its program `../../keyframe/look.py`", brief)
        self.assertNotIn("../v01/", brief)
        # the same bytes are `look.py` in the workspace, to import rather than copy
        self.assertIn("`from look import ...` its palette and type constants", brief)
        self.assertEqual((self.root / "vfx" / "v02" / "look.py").read_bytes(),
                         (self.root / "keyframe" / "look.py").read_bytes())
        review = self.reviews()[0]["prompt"]
        self.assertIn("approved section 1", review)
        self.assertIn(str(self.root / "keyframe" / "look.py"), review)
        # a departure's fix is the module's construct, imported, not a copy of it
        self.assertIn("construct from `look.py` (import it", review)
        self.assertNotIn("copy the approved", review)

    def test_a_look_without_a_program_is_reviewed_without_a_module_to_import(self):
        # an approval recorded before the program was kept: the brief falls
        # back to the constructions, and the review names no `look.py` to import
        lead = self.lead()
        self.look_record(lead, "approved")
        record = json.loads((self.root / "keyframe.json").read_text())
        record["look_program"] = ""
        (self.root / "keyframe.json").write_text(json.dumps(record))
        (self.root / "keyframe" / "look.py").unlink()
        FakeEngine.docs = [TWO_DOC]
        self.author(self.planner(revisions=1))
        brief = (self.root / "vfx" / "v02" / "brief-02.md").read_text()
        self.assertIn("Reuse its constructions", brief)
        self.assertNotIn("look.py", brief)
        review = self.reviews()[0]["prompt"]
        self.assertIn("approved section 1", review)
        self.assertIn("the approved section's construct", review)
        self.assertNotIn("look.py", review)

    def test_a_pending_or_rejected_look_is_not_carried(self):
        for state in ("pending", "rejected"):
            with self.subTest(state=state):
                FakeEngine.stream_calls = []
                lead = self.author(self.planner(lead=1))[0]
                self.look_record(lead, state)
                FakeEngine.stream_calls, FakeEngine.docs = [], [WORLD_DOC, TWO_DOC]
                authored = self.author(self.planner(revisions=0, fresh=True, jobs=1))
                self.assertFalse([r for r in authored if r.get("carried_from")])
                self.assertEqual(len(self.authors()), 2)

    def test_fresh_replans_so_the_approved_section_is_authored_again(self):
        lead = self.lead()
        self.look_record(lead, "approved")
        FakeEngine.docs = [WORLD_DOC, TWO_DOC]
        authored = self.author(self.planner(revisions=0, fresh=True, jobs=1))
        self.assertFalse([r for r in authored if r.get("carried_from")])
        self.assertEqual(len(self.authors()), 2)
        # the look still leads the other briefs
        self.assertIn("## The approved look (section 1", (self.root / "vfx" / "v02" / "brief-02.md").read_text())

    def test_asking_for_the_look_section_authors_it_again(self):
        lead = self.lead()
        self.look_record(lead, "approved")
        FakeEngine.docs = [TWO_DOC]
        self.author(self.planner(revisions=0))                      # v02: 1 carried, 2 authored
        FakeEngine.stream_calls, FakeEngine.docs = [], [WORLD_DOC]
        again = self.author(self.planner(revisions=0, sections_only={1}))
        by_n = {r["n"]: r for r in again}
        self.assertNotIn("carried_from", by_n[1])
        self.assertEqual(by_n[2]["carried_from"], "v02")
        self.assertEqual(len(self.authors()), 1)
        # and a later partial run keeps that re-authoring, not the older look
        FakeEngine.stream_calls, FakeEngine.docs = [], [TWO_DOC]
        later = {r["n"]: r for r in self.author(self.planner(revisions=0, sections_only={2}))}
        self.assertEqual(later[1]["carried_from"], by_n[1]["version"])


class Record(Case):
    """keyframe.json for a look from the film: frames, approval, reuse."""

    def test_proof_frames_play_world_by_world_then_by_time(self):
        frames = self.root / "vfx" / "v01" / "frames"
        frames.mkdir(parents=True)
        for name in ("01-open__world-open-2__1s.png", "01-open__world-open__9.5s.png",
                     "01-open__world-open__1.5s.png", "02-two__world-two__1s.png"):
            (frames / name).write_bytes(png(10))
        rec = {"file": "vfx/v01/01-open.scene.xml",
               "worlds": [{"id": "world-open"}, {"id": "world-open-2"}]}
        self.assertEqual([p.name for p in keyframe.proof_frames(self.root, rec)],
                         ["01-open__world-open__1.5s.png", "01-open__world-open__9.5s.png",
                          "01-open__world-open-2__1s.png"])

    def test_the_frame_that_shows_most_stands_for_the_look(self):
        flat, busy = self.root / "a.png", self.root / "b.png"
        flat.write_bytes(png(20, flat=True))
        busy.write_bytes(png(20))
        self.assertEqual(keyframe.pick_frame([flat, busy]), busy)
        self.assertIsNone(keyframe.pick_frame([]))
        sheet = keyframe.contact_sheet([flat, busy], self.root / "k" / "frames.png")
        with Image.open(sheet) as im:
            self.assertEqual(im.size, (4 * 480, 270 + 22))
        self.assertIsNone(keyframe.contact_sheet([], self.root / "none.png"))

    def test_section_look_run_writes_a_pending_record_with_frame_and_sheet(self):
        planner = self.planner(lead=1)
        dossier = planner.dossier
        dossier.save("09-outline", OUTLINE)
        dossier.save("10-style", STYLE)
        dossier.save("11-effects", GROUPS)
        args = argparse.Namespace(fresh=False, quiet=True)
        look = keyframe.SectionLook(args, self.idea, workspace.Project(self.root), section=1,
                                    verbose=False)
        with patch.object(vfx, "planner_from_args", lambda *a, **kw: planner), \
                patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            record = look.run()
        self.assertEqual((record["source"], record["section"], record["version"]),
                         ("section", 1, "v01"))
        self.assertEqual(record["palette_source"], "styles.json")      # AUTO-155
        self.assertEqual(record["approval"]["state"], "pending")
        self.assertEqual(record["frame"], "keyframe/keyframe.png")
        self.assertTrue((self.root / record["frame"]).is_file())
        self.assertTrue((self.root / record["sheet"]).is_file())
        self.assertTrue(record["frames"])
        self.assertEqual(self.reviews(), [])
        self.assertIn("section 1", (self.root / "keyframe.md").read_text())
        # the same look is reused until it is rejected or asked for again
        again = keyframe.SectionLook(args, self.idea, workspace.Project(self.root), section=1,
                                     verbose=False)
        self.assertEqual(again.reusable()["version"], "v01")
        keyframe.approve(self.root)
        look = keyframe.approved_keyframe(self.root)
        self.assertEqual((look["source"], look["section"]), ("section", 1))
        self.assertEqual(look["program"], record["program"])
        self.assertEqual(vfx.look_section(self.root)["n"], 1)
        keyframe.reject(self.root, "warmer")
        self.assertIsNone(keyframe.SectionLook(args, self.idea, workspace.Project(self.root),
                                               section=1, verbose=False).reusable())
        self.assertIsNone(vfx.look_section(self.root))
        fresh = argparse.Namespace(fresh=True, quiet=True)
        self.assertIsNone(keyframe.SectionLook(fresh, self.idea, workspace.Project(self.root),
                                               section=1, verbose=False).reusable())


class Provenance(Case):
    """The look's section enters the film twice only: the first animation after
    the look, and `--relook`. After that the active version is the film, and the
    look's section is a section like any other (the review's findings, reproduced)."""

    def lead_approved(self) -> dict:
        lead = self.author(self.planner(lead=1))[0]
        self.look_record(lead, "approved")
        FakeEngine.stream_calls = []
        return lead

    def vfx_run(self, docs, **kw):
        # one author at a time: the fake hands out `docs` in call order
        FakeEngine.docs = list(docs)
        kw.setdefault("jobs", 1)
        return {r["n"]: r for r in self.author(self.planner(revisions=0, **kw))}

    def write_vfx_json(self, authored: dict) -> None:
        (self.root / "vfx.json").write_text(json.dumps({"authored": list(authored.values())}))

    def test_a_re_authored_look_section_survives_every_later_partial_run(self):
        self.lead_approved()
        self.vfx_run([TWO_DOC])                                          # v02: 1 carried
        asked = self.vfx_run([WORLD_DOC.replace("#AAAAAA", "#BBBBBB")], sections_only={1})[1]
        self.assertTrue(asked.get("reauthored_look"))
        for _ in range(2):                                               # v04, v05
            later = self.vfx_run([TWO_DOC], sections_only={2})
            self.assertIn("#BBBBBB", (self.root / later[1]["file"]).read_text())
        self.write_vfx_json(later)
        self.assertTrue(keyframe.film_carries_look(self.root))         # it stands for the look

    def test_an_animation_made_while_the_look_was_pending_takes_it_with_relook(self):
        lead = self.author(self.planner(lead=1))[0]
        self.look_record(lead, "pending")
        made = self.vfx_run([WORLD_DOC.replace("#AAAAAA", "#CCCCCC"), TWO_DOC])  # v02, no look
        keyframe.approve(self.root)
        self.write_vfx_json(made)
        self.assertFalse(keyframe.film_carries_look(self.root))
        partial = self.vfx_run([TWO_DOC], sections_only={2})             # the active one stays
        self.assertIn("#CCCCCC", (self.root / partial[1]["file"]).read_text())
        FakeEngine.stream_calls = []
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "lower, new look"}]
        relooked = self.vfx_run([TWO_DOC.replace("#AAAAAA", "#DDDDDD")], relook=True)
        self.assertEqual(relooked[1]["carried_from"], lead["version"])
        self.assertNotIn("kept_from", relooked[2])                      # no contest across looks
        self.assertEqual(len(self.authors()), 1)
        self.write_vfx_json(relooked)
        self.assertTrue(keyframe.film_carries_look(self.root))

    def test_a_relook_and_the_first_animation_carry_the_module_into_the_new_version(self):
        # AUTO-77: the approved look is `look.py` in every version made after the approval
        lead = self.author(self.planner(lead=1))[0]
        self.assertFalse((self.root / "vfx" / "v01" / "look.py").exists())   # nothing approved yet
        self.look_record(lead, "pending")
        self.vfx_run([WORLD_DOC.replace("#AAAAAA", "#CCCCCC"), TWO_DOC])
        self.assertFalse((self.root / "vfx" / "v02" / "look.py").exists())   # still pending
        keyframe.approve(self.root)
        FakeEngine.stream_calls = []
        self.vfx_run([TWO_DOC], relook=True)
        approved = (self.root / "keyframe" / "look.py").read_bytes()
        self.assertEqual((self.root / "vfx" / "v03" / "look.py").read_bytes(), approved)
        self.assertEqual((self.root / lead["program"]).read_bytes(), approved)
        brief = (self.root / "vfx" / "v03" / "brief-02.md").read_text()
        self.assertIn("from look import", brief)

    def test_relook_keeps_a_sound_section_over_a_broken_attempt(self):
        self.lead_approved()
        made = self.vfx_run([TWO_DOC])
        FakeEngine.stream_calls, FakeEngine.crash_on = [], 1
        relooked = self.vfx_run([], relook=True)
        self.assertEqual(relooked[2].get("kept_from"), made[2]["version"])
        self.assertFalse([f for f in relooked[2]["findings"] if f.startswith("error")])

    def test_relook_needs_an_approved_look(self):
        with self.assertRaises(ValueError):
            self.vfx_run([], relook=True)

    def test_a_broken_attempt_at_the_look_section_keeps_the_approved_look(self):
        self.lead_approved()
        self.vfx_run([TWO_DOC])                                          # v02: 1 carried
        FakeEngine.stream_calls, FakeEngine.crash_on = [], 1
        kept = self.vfx_run([], sections_only={1})[1]
        self.assertTrue(kept.get("kept_from"))
        self.assertTrue(kept.get("look"))
        self.assertNotIn("reauthored_look", kept)
        self.assertFalse([f for f in kept["findings"] if f.startswith("error")])

    def test_a_plain_run_replays_the_cached_animation(self):
        self.vfx_run([WORLD_DOC, TWO_DOC])                               # a film with no look
        lead = self.author(self.planner(lead=1))[0]
        self.look_record(lead, "approved")
        FakeEngine.stream_calls = []
        self.vfx_run([])
        self.assertEqual(self.authors(), [])                             # --relook is the way in

    def test_a_look_framed_for_another_size_is_not_carried(self):
        lead = self.author(self.planner(lead=1))[0]
        record = self.look_record(lead, "approved")
        record["frame_size"] = [1080, 1920]
        (self.root / "keyframe.json").write_text(json.dumps(record))
        FakeEngine.stream_calls = []
        after = self.vfx_run([WORLD_DOC, TWO_DOC])
        self.assertNotIn("carried_from", after[1])

    def test_the_look_pass_revises_on_gate_errors_without_a_review(self):
        found = [["error: hero-ball is never inside the camera's view"], []]
        planner = self.planner(lead=1)
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings",
                             lambda *a, **k: found.pop(0) if found else []):
            lead = planner.author(OUTLINE, GROUPS, STYLE, {"ink": "#FFFFFF"})[0]
        self.assertEqual(len(self.authors()), 2)
        self.assertEqual(self.reviews(), [])
        self.assertEqual(lead["revisions"], 1)
        self.assertFalse([f for f in lead["findings"] if f.startswith("error")])

    def test_a_fresh_look_pass_forgets_the_authoring_made_under_the_old_plan(self):
        planner = self.planner(lead=1, fresh=True)
        planner.dossier.save("12-author", [{"n": 1}])
        with patch.object(planner, "outline", lambda: OUTLINE), \
                patch.object(planner, "style", lambda: STYLE), \
                patch.object(planner, "effects", lambda o, s: GROUPS), \
                patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            planner.run_lead()
        self.assertIsNone(planner.dossier.load("12-author"))


class Approvable(Case):
    """A look the gate failed can be neither approved nor carried."""

    def test_errors_or_no_frame_refuse_approval_and_ask_for_another_pass(self):
        lead = self.author(self.planner(lead=1))[0]
        record = self.look_record(lead, "pending")
        record["errors"] = ["error: frame at 1s of world-open: flat"]
        (self.root / "keyframe.json").write_text(json.dumps(record))
        with self.assertRaises(keyframe.KeyframeError):
            keyframe.approve(self.root)
        self.assertTrue(keyframe.look_needs_redo(self.root))
        self.assertEqual(cli.look_gate("keyframe", workspace.Project(self.root), set()), "again")
        record["errors"], record["frame"] = [], ""
        (self.root / "keyframe.json").write_text(json.dumps(record))
        with self.assertRaises(keyframe.KeyframeError):
            keyframe.approve(self.root)
        # and a carried record with an error is never the look
        record = dict(self.look_record(lead, "approved"))
        record["record"] = dict(lead, findings=["error: boom"])
        (self.root / "keyframe.json").write_text(json.dumps(record))
        self.assertIsNone(vfx.look_section(self.root))

    def test_the_section_named_once_stays_the_source(self):
        lead = self.author(self.planner(lead=1))[0]
        record = self.look_record(lead, "rejected")
        record["asked_section"] = 3
        (self.root / "keyframe.json").write_text(json.dumps(record))
        look = keyframe.SectionLook(argparse.Namespace(fresh=False, quiet=True), self.idea,
                                    workspace.Project(self.root), section=None, verbose=False)
        self.assertEqual(look.section, 3)

    def test_an_approved_look_is_kept_by_a_downstream_re_run(self):
        from tests.test_vfx_world import approve_keyframe
        approve_keyframe(self.root)                       # an approved --sheet look
        args = argparse.Namespace(fresh=False, quiet=True, section=None, budget_usd=None)
        with patch.object(keyframe.SectionLook, "run",
                          side_effect=AssertionError("authored a new look")):
            code = keyframe.run_section_look(args, self.idea, workspace.Project(self.root),
                                             verbose=False)
        self.assertEqual(code, 0)
        self.assertEqual(keyframe.approval_state(self.root)["state"], "approved")
        asked = argparse.Namespace(fresh=False, quiet=True, section=2, budget_usd=None)
        with patch.object(keyframe.SectionLook, "run",
                          side_effect=RuntimeError("authoring the asked section")):
            with self.assertRaises(RuntimeError):
                keyframe.run_section_look(asked, self.idea, workspace.Project(self.root),
                                          verbose=False)


class RunStops(Case):
    """`yt run` waits for the look before it pays for the animation."""

    def test_a_pending_look_stops_the_run_before_vfx(self):
        lead = self.author(self.planner(lead=1))[0]
        project = workspace.Project(self.root)
        said = []
        self.look_record(lead, "pending")
        self.assertEqual(cli.look_gate("vfx", project, set(), say=said.append), "stop")
        self.assertIn("--approve", said[0])
        self.assertEqual(cli.look_gate("vfx", project, {"keyframe"}), "")
        self.assertEqual(cli.look_gate("keyframe", project, set()), "")
        keyframe.approve(self.root)
        self.assertEqual(cli.look_gate("vfx", project, set()), "")
        keyframe.reject(self.root, "no")
        self.assertEqual(cli.look_gate("keyframe", project, set()), "again")
        self.assertEqual(cli.look_gate("build", project, set()), "")

    def test_a_pending_look_holds_even_a_done_animation_and_names_how_to_go_on(self):
        lead = self.author(self.planner(lead=1))[0]
        project = workspace.Project(self.root)
        (self.root / "vfx.json").write_text(json.dumps({"authored": [{"n": 1, "file": "x"}]}))
        self.look_record(lead, "pending")
        said = []
        self.assertEqual(cli.look_gate("vfx", project, set(), say=said.append), "stop")
        self.assertEqual(cli.look_gate("vfx", project, set(), {"all"}, say=said.append), "stop")
        self.assertIn("--redo vfx,sound,build,render", said[-1])
        self.assertEqual(cli.look_gate("vfx", project, set(), {"vfx"}, say=said.append), "stop")
        self.assertIn("--redo vfx`", said[-1])
        keyframe.approve(self.root)
        self.assertEqual(cli.look_gate("vfx", project, set()), "relook")   # x is not the look
        (self.root / "vfx.json").write_text(json.dumps({"authored": [lead]}))
        self.assertEqual(cli.look_gate("vfx", project, set()), "")

    def test_doctor_names_relook_and_a_failed_look(self):
        lead = self.author(self.planner(lead=1))[0]
        record = self.look_record(lead, "approved")
        self.assertTrue(keyframe.film_carries_look(self.root, {"authored": [lead]}))
        self.assertFalse(keyframe.film_carries_look(self.root, {"authored": [dict(lead, file="x")]}))
        record["errors"] = ["error: flat"]
        record["approval"]["state"] = "failed"
        (self.root / "keyframe.json").write_text(json.dumps(record))
        self.assertEqual(keyframe.approval_state(self.root)["state"], "failed")
        self.assertTrue(keyframe.look_needs_redo(self.root))
        self.assertNotIn("--approve", (keyframe.write_sheet(self.root, record)).read_text())

    def test_a_section_look_on_the_fallback_palette_is_refused_not_redone(self):
        # AUTO-155: the look is sound, the palette is not the film's; the
        # fix is styles, so `yt run` must not author the look again
        lead = self.author(self.planner(lead=1))[0]
        record = self.look_record(lead, "pending")
        record["palette_source"] = "fallback"
        (self.root / "keyframe.json").write_text(json.dumps(record))
        self.assertFalse(keyframe.look_needs_redo(self.root))
        with self.assertRaises(keyframe.KeyframeError) as ctx:
            keyframe.approve(self.root)
        self.assertIn("yt styles --rich", str(ctx.exception))
        sheet = keyframe.write_sheet(self.root, record).read_text()
        self.assertNotIn("--approve", sheet)
        self.assertIn("fallback palette", sheet)

    def test_a_fallback_look_stops_the_run_naming_styles_not_the_approval(self):
        # AUTO-155: `yt run --skip styles` reaches vfx with a look that
        # `yt keyframe --approve` refuses, so the stop names the palette fix
        lead = self.author(self.planner(lead=1))[0]
        project = workspace.Project(self.root)
        record = self.look_record(lead, "pending")
        record["palette_source"] = "fallback"
        (self.root / "keyframe.json").write_text(json.dumps(record))
        said = []
        self.assertEqual(cli.look_gate("vfx", project, set(), say=said.append), "stop")
        self.assertNotIn("--approve", said[0])
        self.assertIn("fallback palette", said[0])
        self.assertIn(f"yt styles --project {self.root} --rich", said[0])
        self.assertIn(f"yt run --project {self.root} --redo keyframe`", said[0])
        self.assertIn("--skip keyframe", said[0])
        self.assertEqual(cli.look_gate("vfx", project, set(), {"all"}, say=said.append), "stop")
        self.assertIn("--redo keyframe,vfx,sound,build,render`", said[-1])
        self.assertEqual(cli.look_gate("vfx", project, {"keyframe"}), "")


class FallbackSection(Case):
    """A section look authored with no palette of the film's own records it,
    from the styles document its planner read (AUTO-155)."""

    def run_look(self, args):
        planner = self.planner(lead=1)
        planner.dossier.save("09-outline", OUTLINE)
        planner.dossier.save("10-style", STYLE)
        planner.dossier.save("11-effects", GROUPS)
        look = keyframe.SectionLook(args, self.idea, workspace.Project(self.root), section=1,
                                    verbose=False)
        with patch.object(vfx, "planner_from_args", lambda *a, **kw: planner), \
                patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return look.run()

    def test_a_section_look_with_no_styles_json_is_fallback_and_refused(self):
        (self.root / "styles.json").unlink()
        record = self.run_look(argparse.Namespace(fresh=False, quiet=True))
        self.assertEqual(record["palette_source"], "fallback")
        self.assertEqual(record["approval"]["state"], "pending")
        with self.assertRaises(keyframe.KeyframeError) as ctx:
            keyframe.approve(self.root)
        self.assertIn("yt styles --rich", str(ctx.exception))

    def test_the_styles_given_with_in_styles_is_the_palette_recorded(self):
        # the planner reads --in-styles when given; the record must too
        given = self.root / "elsewhere.json"
        given.write_bytes((self.root / "styles.json").read_bytes())
        (self.root / "styles.json").unlink()
        record = self.run_look(argparse.Namespace(fresh=False, quiet=True,
                                                  in_styles=str(given)))
        self.assertEqual(record["palette_source"], "styles.json")
        (self.root / "styles.json").write_bytes(given.read_bytes())
        given.write_text(json.dumps({"styles": [{"name": "plain", "palette": "warm greys"}]}))
        record = self.run_look(argparse.Namespace(fresh=True, quiet=True,
                                                  in_styles=str(given)))
        self.assertEqual(record["palette_source"], "fallback")


if __name__ == "__main__":
    unittest.main()
