"""A vfx section carried forward keeps its review only while its inputs are
the ones that review judged (AUTO-114).

`yt vfx --sections N` carries every section not named, and `--relook` the
approved look's, by copying the old record. The copy used to keep `score`,
`review` and `best_round` whatever had changed since: a section whose
narration was rewritten kept a review of a screenplay that no longer
exists, and the competition compared that old score with a new attempt
judged against the new one. Each section record now carries
`inputs_sha256`, a digest of the JSON the author was handed (the section's
shots with their measured timings, its effects group, the style tokens and
the approved look's digest). A carried record whose digest differs moves
its review under `inherited` and says it was not reviewed against the
current inputs; no model is asked. The model is never called here:
`harness.ENGINE` is the fake of tests/test_vfx_world.py.
"""

from __future__ import annotations

import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_look_section as look
from tests import test_vfx_world as world
from tests.test_vfx_world import EFFECTS, SECTION, STYLE, WORLD_DOC, FakeEngine, varied_png
from ytstudio import doctor, harness, vfx, workspace
from ytstudio.core import Idea, write_json
from ytstudio.research import Dossier

GROUP = {"section": "Open", "seconds": 15, "effects": EFFECTS, "cuts": []}
TOKENS = {"ink": "#FFFFFF"}


def renarrated(section: dict, label: str = "1.2", words: str = "a new line") -> dict:
    out = copy.deepcopy(section)
    for shot in out["shots"]:
        if shot["label"] == label:
            shot["narration"] = words
    return out


def second(section: dict) -> dict:
    """The fixture section as a second section, "Two", with shots 2.x."""
    out = copy.deepcopy(section)
    out["heading"] = "Two"
    for shot in out["shots"]:
        shot["label"] = "2" + shot["label"][1:]
    return out


EFFECTS_TWO = [dict(e, id=e["id"].replace("open", "two"), section="Two",
                    shots=["2" + s[1:] for s in e["shots"]]) for e in EFFECTS]
GROUP_TWO = {"section": "Two", "seconds": 15, "effects": EFFECTS_TWO, "cuts": []}


class Digest(unittest.TestCase):
    def digest(self, section=SECTION, group=GROUP, tokens=TOKENS, look="") -> str:
        return vfx.section_inputs_digest(section, group, tokens, look)

    def test_it_is_a_sha256_stable_across_key_order(self):
        base = self.digest()
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        shuffled = {k: SECTION[k] for k in reversed(list(SECTION))}
        shuffled["shots"] = [{k: s[k] for k in reversed(list(s))} for s in SECTION["shots"]]
        group = {k: GROUP[k] for k in reversed(list(GROUP))}
        tokens = {"b": "#000000", "a": "#FFFFFF"}
        self.assertEqual(self.digest(shuffled, group), base)
        self.assertEqual(self.digest(tokens=tokens),
                         self.digest(tokens={"a": "#FFFFFF", "b": "#000000"}))
        self.assertEqual(self.digest(), base)

    def test_it_changes_with_what_the_author_was_handed(self):
        base = self.digest()
        retimed = copy.deepcopy(SECTION)
        retimed["shots"][1]["seconds"] = 4.5
        moved = copy.deepcopy(SECTION)
        moved["shots"][2]["at"] = 7.25
        redrawn = copy.deepcopy(SECTION)
        redrawn["shots"][0]["visual"] = "a different picture"
        replanned = dict(GROUP, effects=EFFECTS[:2])
        cut = dict(GROUP, cuts=[{"before": "1.3"}])
        for changed in (self.digest(renarrated(SECTION)), self.digest(retimed),
                        self.digest(moved), self.digest(redrawn),
                        self.digest(group=replanned), self.digest(group=cut),
                        self.digest(tokens={"ink": "#000000"}),
                        self.digest(look="abc123")):
            self.assertNotEqual(changed, base)

    def test_moving_the_whole_section_on_the_film_clock_changes_nothing(self):
        # an earlier section re-voiced longer moves every later shot; the
        # section's own words, pictures and timings are what its review judged
        later = copy.deepcopy(SECTION)
        for shot in later["shots"]:
            shot["at"] = float(shot["at"]) + 12.5
        self.assertEqual(self.digest(later), self.digest())

    def test_an_integer_and_a_float_second_are_the_same_input(self):
        floats = copy.deepcopy(SECTION)
        for shot in floats["shots"]:
            shot["at"], shot["seconds"] = float(shot["at"]), float(shot["seconds"])
        self.assertEqual(self.digest(floats), self.digest())


def reviewed_record(**over) -> dict:
    record = {"n": 2, "section": "Two", "file": "vfx/v01/02-two.scene.xml", "version": "v01",
              "score": 8, "review": {"score": 8, "summary": "good"}, "best_round": 1,
              "findings": [], "inputs_sha256": "a" * 64}
    record.update(over)
    return record


class Inherit(unittest.TestCase):
    """vfx.inherit_review: the rule every carry follows."""

    def test_a_matching_digest_keeps_the_review(self):
        record = reviewed_record()
        self.assertEqual(vfx.inherit_review(record, "a" * 64), record)

    def test_a_changed_digest_moves_the_review_under_inherited(self):
        out = vfx.inherit_review(reviewed_record(), "b" * 64)
        self.assertIsNone(out["score"])
        self.assertIsNone(out["review"])
        self.assertIsNone(out["best_round"])
        self.assertIs(out["reviewed"], False)
        self.assertEqual(out["inputs_sha256"], "b" * 64)
        self.assertEqual(out["inherited"], {"score": 8, "review": {"score": 8, "summary": "good"},
                                            "best_round": 1, "from_version": "v01",
                                            "inputs_sha256": "a" * 64})
        self.assertIn("warning: carried: section 2's inputs changed since its review; "
                      "it was not reviewed against them", out["findings"])
        self.assertFalse(vfx.has_errors(out))          # it stays in the film

    def test_a_record_without_a_digest_counts_as_changed(self):
        record = reviewed_record()
        del record["inputs_sha256"]
        out = vfx.inherit_review(record, "b" * 64)
        self.assertIs(out["reviewed"], False)
        self.assertIsNone(out["inherited"]["inputs_sha256"])
        self.assertEqual(out["inherited"]["score"], 8)

    def test_carried_again_it_keeps_the_first_inherited_review_and_one_warning(self):
        once = vfx.inherit_review(reviewed_record(), "b" * 64)
        again = vfx.inherit_review(dict(once, version="v02"), "c" * 64)
        self.assertEqual(again["inherited"]["score"], 8)
        self.assertEqual(again["inherited"]["from_version"], "v01")
        self.assertEqual(sum(1 for f in again["findings"] if f.startswith("warning: carried")), 1)
        # carried with the inputs it was marked against, it stays unreviewed
        self.assertEqual(vfx.inherit_review(once, "b" * 64), once)

    def test_an_inherited_score_has_no_say(self):
        out = vfx.inherit_review(reviewed_record(), "b" * 64)
        self.assertFalse(vfx.passes_gate(out))
        self.assertFalse(vfx.keeps_previous(out, {"score": 5, "findings": []}))
        self.assertIsNone(vfx.best_round([dict(out, round=1)]))


class Carry(unittest.TestCase):
    """The carry path and the competition of VfxPlanner.author."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = FakeEngine
        FakeEngine.stream_calls, FakeEngine.json_calls, FakeEngine.reviews = [], [], []
        FakeEngine.doc = WORLD_DOC
        FakeEngine.time_out_on = 0
        FakeEngine.docs, FakeEngine.crash_on = [], 0
        FakeEngine.stale_frame = False
        self.planner = self.make(set())

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def make(self, sections_only: set[int], **kw) -> vfx.VfxPlanner:
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        return vfx.VfxPlanner(bundle, Dossier(self.root, idea, at=self.root),
                              vfx.Workspace(self.root), model="sonnet", sections=[SECTION],
                              revisions=0, author_bar=7.0, verbose=False,
                              sections_only=sections_only, **kw)

    @contextlib.contextmanager
    def stubbed(self):
        """No render host: validation passes, every still is a varied picture."""
        real = world.author_program

        def program(cwd, name, doc):
            # each section's world is named after its heading
            real(cwd, name, doc.replace("world-open", "world-" + name[3:-10]))

        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered locally")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.object(world, "author_program", program):
            yield

    def author(self, sections=(SECTION,), groups=(GROUP,), tokens=TOKENS):
        with self.stubbed():
            return self.planner.author({"sections": list(sections)}, list(groups), STYLE,
                                       dict(tokens))

    def run_vfx(self, sections=(SECTION,), groups=(GROUP,)) -> dict:
        """VfxPlanner.run with the plan's stages answered: vfx.json's data."""
        stubs = {"outline": {"sections": list(sections)}, "style": STYLE,
                 "effects": list(groups), "continuity": {}, "scene_map": {}, "reach": {},
                 "plan": {}}
        with self.stubbed(), patch.multiple(self.planner, **{
                k: (lambda *a, v=v, **kw: v) for k, v in stubs.items()}):
            return self.planner.run()

    def first(self, score: float = 8) -> dict:
        FakeEngine.reviews = [{"frames": [], "score": score, "summary": "first"}]
        return self.author()[0]

    # -- every record says what it judged ------------------------------------

    def test_an_authored_record_carries_its_inputs_digest(self):
        record = self.first()
        self.assertEqual(record["inputs_sha256"],
                         vfx.section_inputs_digest(SECTION, GROUP, TOKENS, ""))
        saved = vfx.Workspace(self.root, version="v01").read_record(Path(record["file"]).name)
        self.assertEqual(saved["inputs_sha256"], record["inputs_sha256"])

    def test_a_failed_authoring_carries_it_too(self):
        with patch.object(self.planner, "_author_one", side_effect=RuntimeError("boom")):
            record = self.author()[0]
        self.assertEqual(record["inputs_sha256"],
                         vfx.section_inputs_digest(SECTION, GROUP, TOKENS, ""))

    # -- the carry ------------------------------------------------------------

    def test_a_carried_section_with_the_same_inputs_keeps_its_review(self):
        first = self.first()
        self.planner = self.make({2})
        carried = self.author()[0]
        self.assertEqual(carried["carried_from"], "v01")
        self.assertEqual(carried["score"], 8)
        self.assertEqual(carried["review"], first["review"])
        self.assertNotIn("inherited", carried)
        self.assertNotIn("reviewed", carried)

    def test_a_carried_section_whose_narration_changed_is_unreviewed(self):
        first = self.first()
        FakeEngine.stream_calls = []
        self.planner = self.make({2})
        carried = self.author(sections=(renarrated(SECTION),))[0]
        self.assertFalse(FakeEngine.stream_calls)                    # no model was asked
        self.assertEqual(carried["version"], "v02")
        self.assertEqual(carried["carried_from"], "v01")
        self.assertTrue((self.root / carried["file"]).is_file())     # the files come along
        self.assertIsNone(carried["score"])
        self.assertIsNone(carried["review"])
        self.assertIsNone(carried["best_round"])
        self.assertIs(carried["reviewed"], False)
        self.assertEqual(carried["inherited"]["score"], 8)
        self.assertEqual(carried["inherited"]["review"], first["review"])
        self.assertEqual(carried["inherited"]["from_version"], "v01")
        self.assertEqual(carried["inherited"]["inputs_sha256"], first["inputs_sha256"])
        self.assertEqual(carried["inputs_sha256"],
                         vfx.section_inputs_digest(renarrated(SECTION), GROUP, TOKENS, ""))
        self.assertIn("warning: carried: section 1's inputs changed since its review; "
                      "it was not reviewed against them", carried["findings"])
        # the section's record on disk says the same
        saved = vfx.Workspace(self.root, version="v02").read_record(Path(carried["file"]).name)
        self.assertIs(saved["reviewed"], False)

    def test_new_tokens_unreview_a_carried_section(self):
        self.first()
        self.planner = self.make({2})
        carried = self.author(tokens={"ink": "#000000"})[0]
        self.assertIs(carried["reviewed"], False)

    def test_a_carried_record_without_a_digest_counts_as_changed(self):
        first = self.first()
        stripped = dict(first)
        del stripped["inputs_sha256"]
        self.planner.dossier.save("12-author", [stripped])
        self.planner = self.make({2})
        carried = self.author()[0]
        self.assertIs(carried["reviewed"], False)
        self.assertEqual(carried["inherited"]["score"], 8)

    def test_only_the_changed_section_loses_its_review(self):
        """The card's acceptance: the script changed section 2's narration and
        the operator asks for section 3; section 1 keeps its review."""
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "ok"}] * 2
        self.author(sections=(SECTION, second(SECTION)), groups=(GROUP, GROUP_TWO))
        self.planner = self.make({3})
        out = {r["n"]: r for r in self.author(
            sections=(SECTION, renarrated(second(SECTION), "2.2")), groups=(GROUP, GROUP_TWO))}
        self.assertEqual(out[1]["score"], 8)
        self.assertNotIn("inherited", out[1])
        self.assertIs(out[2]["reviewed"], False)
        self.assertEqual(out[2]["inherited"]["score"], 8)

    # -- the competition ------------------------------------------------------

    def test_a_stale_previous_version_does_not_beat_a_sound_attempt_on_score(self):
        self.first(score=8)
        self.planner = self.make({1})
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "worse"}]
        out = self.author(sections=(renarrated(SECTION),))[0]
        self.assertEqual(out["version"], "v02")
        self.assertEqual(out["score"], 5)
        self.assertNotIn("kept_from", out)

    def test_a_previous_version_with_the_same_inputs_still_competes_on_score(self):
        self.first(score=8)
        self.planner = self.make({1})
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "worse"}]
        out = self.author()[0]
        self.assertEqual(out["kept_from"], "v01")
        self.assertEqual(out["score"], 8)

    def test_a_stale_previous_version_kept_over_a_broken_attempt_is_unreviewed(self):
        self.first(score=8)
        self.planner = self.make({1})
        with patch.object(self.planner, "_author_one", side_effect=RuntimeError("boom")):
            out = self.author(sections=(renarrated(SECTION),))[0]
        self.assertEqual(out["kept_from"], "v01")
        self.assertIsNone(out["score"])
        self.assertIs(out["reviewed"], False)
        self.assertEqual(out["inherited"]["score"], 8)
        self.assertEqual(out["inputs_sha256"],
                         vfx.section_inputs_digest(renarrated(SECTION), GROUP, TOKENS, ""))

    def test_an_unreviewed_previous_version_is_kept_over_a_broken_attempt(self):
        # v02 carried section 1 unreviewed; its digest now matches, but its
        # score is None: a broken attempt must not replace a sound section
        self.first(score=8)
        self.planner = self.make({2})
        self.author(sections=(renarrated(SECTION),))
        self.planner = self.make({1})
        with patch.object(self.planner, "_author_one", side_effect=RuntimeError("boom")):
            out = self.author(sections=(renarrated(SECTION),))[0]
        self.assertEqual(out["kept_from"], "v02")
        self.assertFalse(vfx.has_errors(out))
        self.assertIs(out["reviewed"], False)
        self.assertEqual(out["inherited"]["score"], 8)

    def test_a_sound_attempt_replaces_an_unreviewed_previous_version(self):
        self.first(score=8)
        self.planner = self.make({2})
        self.author(sections=(renarrated(SECTION),))
        self.planner = self.make({1})
        FakeEngine.reviews = [{"frames": [], "score": 5, "summary": "sound"}]
        out = self.author(sections=(renarrated(SECTION),))[0]
        self.assertEqual((out["version"], out["score"]), ("v03", 5))
        self.assertNotIn("kept_from", out)

    def test_inherited_names_the_version_that_made_the_review(self):
        # reviewed in v01, carried whole into v02, then carried after a change
        self.first(score=8)
        self.planner = self.make({2})
        self.author()
        self.planner = self.make({2})
        carried = self.author(sections=(renarrated(SECTION),))[0]
        self.assertEqual(carried["version"], "v03")
        self.assertEqual(carried["inherited"]["from_version"], "v01")

    # -- --recover ------------------------------------------------------------

    def test_recover_keeps_a_section_carried_without_its_review(self):
        # `--sections` killed after the carry: its record in this version is
        # an inherited carry, sound and whole, not a section never reviewed
        self.first(score=8)
        self.planner = self.make({2})
        self.author(sections=(renarrated(SECTION),))
        self.planner = self.make(set(), recover=True)
        out = self.planner.recovered({"sections": [renarrated(SECTION)]}, [GROUP])[0]
        self.assertEqual(out["version"], "v02")
        self.assertFalse(vfx.has_errors(out), out["findings"])
        self.assertIsNone(out["score"])
        self.assertIs(out["reviewed"], False)
        self.assertEqual(out["inherited"]["score"], 8)
        self.assertTrue((self.root / out["file"]).is_file())

    # -- vfx.json and doctor --------------------------------------------------

    def test_vfx_json_and_doctor_name_the_section_carried_without_its_review(self):
        """The card's acceptance end to end: `yt vfx --sections 3` after section
        2's narration changed, through run() and write_vfx to `yt doctor`."""
        FakeEngine.reviews = [{"frames": [], "score": 8, "summary": "ok"}] * 2
        groups = (GROUP, GROUP_TWO)
        vfx.write_vfx(self.planner.dossier, self.run_vfx((SECTION, second(SECTION)), groups))
        self.planner = self.make({3})
        vfx.write_vfx(self.planner.dossier, self.run_vfx(
            (SECTION, renarrated(second(SECTION), "2.2")), groups))
        data = json.loads((self.planner.dossier.dir / "vfx.json").read_text(encoding="utf-8"))
        one, two = data["authored"]
        self.assertEqual(one["score"], 8)
        self.assertNotIn("inherited", one)
        self.assertIs(two["reviewed"], False)
        self.assertEqual(two["inherited"]["score"], 8)
        self.assertIsNone(data["authoring"]["scores"][two["file"]])
        self.assertEqual(data["authoring"]["scores"][one["file"]], 8)
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})
        report = doctor.check_project(workspace.Project(self.planner.dossier.dir))
        fixes = [f.fix for f in report.findings if f.area == "vfx" and f.level == "warn"]
        self.assertTrue(any(f.endswith("--sections 2") for f in fixes), fixes)
        self.assertFalse(any(f.endswith("--sections 1") for f in fixes), fixes)

    def test_keep_previous_applies_the_same_rule(self):
        first = self.first(score=8)
        ws = vfx.Workspace(self.root)
        ws.new_version()
        attempt = dict(first, file=f"{ws.rel}/{Path(first['file']).name}", version=ws.version,
                       score=None, findings=["error: boom"])
        same = ws.keep_previous(first, attempt, 1, inputs_sha256=first["inputs_sha256"])
        self.assertEqual(same["score"], 8)
        changed = ws.keep_previous(first, attempt, 1, inputs_sha256="b" * 64)
        self.assertIsNone(changed["score"])
        self.assertEqual(changed["inherited"]["score"], 8)
        self.assertEqual(changed["kept_from"], "v01")


TOKENS_RUN = vfx.tokens_from(STYLE)


class LookCarry(look.Case):
    """The approved look's section is made in the look pass, against no look:
    its digest names none, so carrying it into the film (the first animation,
    `--relook`) keeps its standing while its own inputs are unchanged."""

    def film(self, planner, docs=(), outline=look.OUTLINE) -> dict[int, dict]:
        FakeEngine.docs = list(docs)
        stubs = {"outline": outline, "style": STYLE, "effects": look.GROUPS,
                 "continuity": {}, "scene_map": {}, "reach": {}, "plan": {}}
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []), \
                patch.multiple(planner, **{k: (lambda *a, v=v, **kw: v)
                                           for k, v in stubs.items()}):
            data = planner.run()
        return {r["n"]: r for r in data["authored"]}

    def lead_approved(self, outline=look.OUTLINE) -> dict:
        planner = self.planner(lead=1)
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (varied_png(), "rendered")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            lead = planner.author(outline, look.GROUPS, STYLE, dict(TOKENS_RUN))[0]
        self.look_record(lead, "approved")
        FakeEngine.stream_calls = []
        return lead

    def doctor_vfx(self, authored: dict[int, dict]):
        write_json(self.root / "idea.json", {"title": "T", "genre": "g", "description": "d"})
        write_json(self.root / "vfx.json", {"totals": {}, "plan": {},
                                            "authored": list(authored.values())})
        return [f for f in doctor.check_project(workspace.Project(self.root)).findings
                if f.area == "vfx" and ("review" in f.message or "inputs" in f.message)]

    def test_the_approved_look_carried_into_the_film_keeps_its_standing(self):
        lead = self.lead_approved()
        made = self.film(self.planner(revisions=0, jobs=1), [look.TWO_DOC])
        self.assertEqual(made[1]["carried_from"], lead["version"])
        self.assertTrue(made[1].get("look"))
        self.assertNotIn("inherited", made[1])
        self.assertNotIn("reviewed", made[1])
        relooked = self.film(self.planner(revisions=0, jobs=1, relook=True), [look.TWO_DOC])
        self.assertEqual(relooked[1]["carried_from"], lead["version"])
        self.assertNotIn("inherited", relooked[1])
        self.assertNotIn("reviewed", relooked[1])
        self.assertEqual(self.doctor_vfx(relooked), [])

    def test_a_look_whose_narration_changed_is_carried_unreviewed_and_doctor_says_so(self):
        self.lead_approved()
        changed = {"sections": [renarrated(SECTION), look.TWO]}
        made = self.film(self.planner(revisions=0, jobs=1), [look.TWO_DOC], outline=changed)
        self.assertIs(made[1]["reviewed"], False)
        self.assertIsNone(made[1]["inherited"]["score"])
        said = self.doctor_vfx(made)
        self.assertEqual(len(said), 1)
        # the look was approved, never scored: not a review lost, and not a
        # call to re-author the operator's look as if it were one
        self.assertEqual(said[0].level, "note")
        self.assertIn("approved look", said[0].message)
        self.assertFalse(said[0].fix.endswith("--sections 1"))

    def test_a_recovered_section_names_the_inputs_it_was_authored_against(self):
        self.lead_approved()
        digest = vfx.approved_look(self.root)["digest"]
        self.assertTrue(digest)
        made = self.film(self.planner(revisions=0, jobs=1), [look.TWO_DOC])
        self.assertEqual(made[2]["inputs_sha256"],
                         vfx.section_inputs_digest(look.TWO, look.GROUPS[1], TOKENS_RUN, digest))
        # a run from before section records: the rounds and cached reviews decide
        (self.root / "vfx" / "v02" / "rounds" / "02-two" / "record.json").unlink()
        FakeEngine.stream_calls = []
        rebuilt = self.film(self.planner(recover=True))
        self.assertEqual(FakeEngine.stream_calls, [])
        self.assertIsNotNone(rebuilt[2]["score"])
        self.assertEqual(rebuilt[2]["inputs_sha256"], made[2]["inputs_sha256"])
        # the carried look comes back as it stands: approved, never scored
        self.assertFalse(vfx.has_errors(rebuilt[1]), rebuilt[1]["findings"])
        # and a later --sections carries both with their standing
        later = self.film(self.planner(revisions=0, jobs=1, sections_only={3}))
        self.assertEqual(later[2]["score"], rebuilt[2]["score"])
        self.assertNotIn("inherited", later[2])
        self.assertNotIn("inherited", later[1])


class Manual(unittest.TestCase):
    def test_the_skill_says_a_carried_section_can_lose_its_review(self):
        text = " ".join((Path(__file__).resolve().parent.parent / "ytstudio" / "SKILL.md")
                        .read_text(encoding="utf-8").split())
        for needle in ("inputs_sha256", "inherited", "`reviewed: false`",
                       "video_sha256", "scene_sha256"):
            self.assertIn(needle, text)


# --------------------------------------------------------------------------- #
# AUTO-149: the headline cards a section stages
# --------------------------------------------------------------------------- #

def article(headline, outlet="Reuters", date="03 Mar 2024", n=0):
    return {"kind": "article", "title": f"{headline} ({outlet})", "source": f"news/{outlet}",
            "licence": "All rights reserved", "credit_line": f"{outlet}, {date}", "note": "",
            "url": f"https://news.example/{n}", "reference_only": True}


ASSETS = [{"kind": "image", "title": "deck", "file": "downloads/deck.jpg"},
          article("Operator skipped inspections for years", n=1),
          article("Inquiry names the engineers", "AP", "15 Jul 2020", n=2)]


def citing(section: dict, label: str, asset: str) -> dict:
    out = copy.deepcopy(section)
    for shot in out["shots"]:
        if shot["label"] == label:
            shot["asset"] = asset
    return out


class HeadlineCards(unittest.TestCase):
    def test_only_the_cited_headlines_are_listed_with_the_rules(self):
        lines = vfx.headline_card_lines(ASSETS, citing(SECTION, "1.2", "headline [N2]")["shots"])
        text = "\n".join(lines)
        self.assertEqual(lines[0], "## Headline cards to typeset")
        self.assertIn("- N2 (shot 1.2): \"Inquiry names the engineers\" — AP, 15 Jul 2020 "
                      "(https://news.example/2)", text)
        self.assertNotIn("Operator skipped", text)
        for words in ("never the outlet's masthead, logo", "screenshot",
                      "\"outlet, date\"", "verbatim", "never paraphrase"):
            self.assertIn(words, text)

    def test_n2_is_the_headline_the_script_brief_called_n2(self):
        from ytstudio import script
        brief = script.research_block({"assets": ASSETS})
        listed = next(line for line in brief.splitlines() if line.startswith("- N2: "))
        card = next(line for line in vfx.headline_card_lines(
            ASSETS, citing(SECTION, "1.3", "headline [N2]")["shots"])
            if line.startswith("- N2"))
        self.assertEqual(listed.split(": ", 1)[1], card.split(": ", 1)[1])

    def test_a_section_citing_none_adds_no_lines(self):
        self.assertEqual(vfx.headline_card_lines(ASSETS, SECTION["shots"]), [])
        self.assertEqual(vfx.headline_card_lines(
            ASSETS, citing(SECTION, "1.2", "headline [N9]")["shots"]), [])

    def test_the_digest_of_a_section_citing_none_is_todays(self):
        press = vfx.press_items(ASSETS)
        self.assertEqual(vfx.section_inputs_digest(SECTION, GROUP, TOKENS, "", press=press),
                         vfx.section_inputs_digest(SECTION, GROUP, TOKENS, ""))

    def test_a_changed_cited_headline_changes_the_digest(self):
        cites = citing(SECTION, "1.2", "headline [N1]")
        press = vfx.press_items(ASSETS)
        before = vfx.section_inputs_digest(cites, GROUP, TOKENS, "", press=press)
        self.assertNotEqual(before, vfx.section_inputs_digest(cites, GROUP, TOKENS, ""))
        changed = vfx.press_items([ASSETS[0], article("Operator skipped checks", n=1),
                                   ASSETS[2]])
        self.assertNotEqual(before,
                            vfx.section_inputs_digest(cites, GROUP, TOKENS, "", press=changed))
        # an uncited headline changing leaves it alone
        other = vfx.press_items([ASSETS[0], ASSETS[1], article("Something else", n=2)])
        self.assertEqual(before,
                         vfx.section_inputs_digest(cites, GROUP, TOKENS, "", press=other))

    def test_the_planner_digests_the_bundles_press(self):
        root = Path(tempfile.mkdtemp())
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": ASSETS, "premise": ""}
        planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                 model="sonnet", sections=[SECTION], revisions=0,
                                 author_bar=7.0, verbose=False)
        cites = citing(SECTION, "1.2", "headline [N1]")
        self.assertEqual(planner._inputs_sha256(SECTION, GROUP),
                         vfx.section_inputs_digest(SECTION, GROUP, {}, ""))
        self.assertEqual(planner._inputs_sha256(cites, GROUP),
                         vfx.section_inputs_digest(cites, GROUP, {}, "",
                                                   press=vfx.press_items(ASSETS)))
        self.assertNotEqual(planner._inputs_sha256(cites, GROUP),
                            vfx.section_inputs_digest(cites, GROUP, {}, ""))

    def test_the_section_brief_carries_the_cards(self):
        ws = vfx.Workspace(Path(tempfile.mkdtemp()), version="v01")
        cites = citing(SECTION, "1.2", "headline [N2]")
        path = ws.brief(1, idea=Idea(title="t", genre="g", description="d"), house_rules="",
                        style=STYLE, tokens=TOKENS, frame=(1920, 1080), section=cites,
                        effects=EFFECTS, words={}, assets=ASSETS)
        self.assertIn("## Headline cards to typeset", path.read_text(encoding="utf-8"))
        path = ws.brief(1, idea=Idea(title="t", genre="g", description="d"), house_rules="",
                        style=STYLE, tokens=TOKENS, frame=(1920, 1080), section=SECTION,
                        effects=EFFECTS, words={}, assets=ASSETS)
        self.assertNotIn("Headline cards", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
