"""AUTO-98: the story's binding flags reach the people who draw the film.

The fact-check already binds the picture (`vfx.picture_constraints` reads
script.json `check`). The story review's flags (`review.flags`, the
coverage-shaped review of narrative.REVIEW_SCHEMA) and the picture-check's
standing flags (`picture.flags`, `yt script --review`) ride the same path:
a blocker or important one reaches the vfx planner, each section's author
brief and reviewer, and the keyframe brief, under `## Story constraints`,
the whole-film ones everywhere and a shot's in the section that holds it.
A minor one binds nothing. The model is never called and no host is used.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytstudio import keyframe, vfx
from ytstudio.core import Idea, write_json
from ytstudio.research import Dossier

from tests.test_keyframe import SCRIPT as KEYFRAME_SCRIPT, ProjectCase
from tests.test_vfx_delta_review import Case as AuthoringCase, review
from tests.test_vfx_world import EFFECTS, SECTION, STYLE

SCRIPT = {"sections": [
    {"heading": "Open", "shots": [{"n": 1, "label": "1.1"}, {"n": 2, "label": "1.2"},
                                  {"n": 3, "label": "1.3"}, {"n": 4, "label": "1.4"}]},
    {"heading": "Two", "shots": [{"n": 5, "label": "2.1"}]}],
    "check": {"verdict": "ok", "flags": [
        {"n": 5, "severity": "blocker", "problem": "shot 2.1 labels a model wrongly",
         "fix": "drop the label"}]},
    "review": {"verdict": "consider", "flags": [
        {"n": 0, "section": 0, "criterion": "stakes", "severity": "important",
         "problem": "the stakes are never shown happening to anyone",
         "fix": "show the people on the bridge"},
        {"n": 2, "section": 1, "criterion": "reveal_setup", "severity": "minor",
         "problem": "a cosmetic quibble", "fix": "-"}]},
    "picture": {"flags": [
        {"n": 3, "severity": "important", "kind": "picture:unshown-claim",
         "problem": "picture:unshown-claim: shot 1.3: the glide is said to lose lift; "
                    "nothing shows the lift", "fix": "make the picture demonstrate it"},
        {"n": 5, "severity": "important", "kind": "picture:bare-number",
         "problem": "picture:bare-number: shot 2.1: 40,000 tonnes, bare",
         "fix": "pair the figure with a comparison"}]}}


class Rows(unittest.TestCase):
    def test_review_and_picture_flags_bind_beside_the_fact_check(self):
        rows = vfx.picture_constraints(SCRIPT)
        got = [(r["source"], r["label"], r["severity"]) for r in rows]
        self.assertEqual(got, [("check", "2.1", "blocker"), ("review", "", "important"),
                               ("picture", "1.3", "important"),
                               ("picture", "2.1", "important")])
        self.assertNotIn("a cosmetic quibble", str(rows))        # minor binds nothing

    def test_the_story_flags_are_their_own_block(self):
        rows = vfx.picture_constraints(SCRIPT)
        one = "\n".join(vfx.constraint_lines(rows, {"1.1", "1.2", "1.3", "1.4"}))
        two = "\n".join(vfx.constraint_lines(rows, {"2.1"}))
        self.assertIn("## Story constraints", one)
        self.assertNotIn("## Fact-check constraints", one)        # 2.1's only
        self.assertIn("whole film: the stakes are never shown happening to anyone", one)
        self.assertIn("REQUIRED: show the people on the bridge", one)
        self.assertIn("shot 1.3: picture:unshown-claim", one)
        self.assertNotIn("40,000 tonnes", one)
        self.assertIn("## Fact-check constraints", two)
        self.assertIn("## Story constraints", two)
        self.assertLess(two.index("## Fact-check constraints"), two.index("## Story constraints"))
        self.assertIn("40,000 tonnes", two.split("## Story constraints")[1])
        self.assertNotIn("labels a model", two.split("## Story constraints")[1])

    def test_a_section_level_story_flag_binds_its_section_only(self):
        # narrative.REVIEW_SCHEMA allows a flag on a section (n 0, section k)
        data = copy.deepcopy(SCRIPT)
        data["review"]["flags"].append(
            {"n": 0, "section": 2, "criterion": "stakes", "severity": "important",
             "problem": "section two never shows who pays", "fix": "show the toll"})
        rows = vfx.picture_constraints(data)
        one = "\n".join(vfx.constraint_lines(rows, {"1.1", "1.2", "1.3", "1.4"}))
        two = "\n".join(vfx.constraint_lines(rows, {"2.1"}))
        every = "\n".join(vfx.constraint_lines(rows))
        self.assertNotIn("never shows who pays", one)
        self.assertIn("section 2: section two never shows who pays", two)
        self.assertIn("section 2: section two never shows who pays", every)
        self.assertIn("whole film: the stakes are never shown", one)   # section 0 still is
        self.assertIn("whole film: the stakes are never shown", two)

    def test_a_script_without_them_reads_as_before(self):
        plain = {"sections": SCRIPT["sections"], "check": SCRIPT["check"]}
        rows = vfx.picture_constraints(plain)
        self.assertEqual([r["source"] for r in rows], ["check"])
        self.assertNotIn("## Story constraints", "\n".join(vfx.constraint_lines(rows)))
        self.assertEqual(vfx.picture_constraints({"sections": [], "review": "x",
                                                  "picture": None}), [])


class SectionBrief(unittest.TestCase):
    def test_the_authors_brief_carries_its_story_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = vfx.Workspace(Path(tmp), version="v01")
            path = ws.brief(1, idea=Idea(title="T", genre="g", description="d"),
                            house_rules="", style=STYLE, tokens={"ink": "#FFFFFF"},
                            frame=(1920, 1080), section=SECTION, effects=EFFECTS, words={},
                            assets=[], worlds=vfx.world_symbols(SECTION, EFFECTS),
                            constraints=vfx.picture_constraints(SCRIPT))
            text = path.read_text(encoding="utf-8")
        self.assertIn("## Story constraints", text)
        self.assertIn("the stakes are never shown happening to anyone", text)
        self.assertIn("nothing shows the lift", text)
        self.assertNotIn("40,000 tonnes", text)                   # section two's

    def test_a_standing_formula_flag_binds_the_brief_and_a_kept_one_does_not(self):
        # AUTO-145: picture:formula rides the existing path; script.json
        # keeps a writer's keep under picture.kept, which binds nothing.
        formula = {"n": 1, "severity": "important", "kind": "picture:formula",
                   "problem": "picture:formula: shot 1.1: the section opens on F = ma",
                   "fix": "show the relation first as a worked case"}
        spatial = {"n": 2, "severity": "important", "kind": "picture:spatial",
                   "problem": "picture:spatial: shot 1.2: the nested shells are a label",
                   "fix": "show it in space", "kept": "the line compares it to bowls"}
        data = copy.deepcopy(SCRIPT)
        data["picture"]["flags"].append(formula)
        data["picture"]["kept"] = [spatial]

        def brief(script):
            with tempfile.TemporaryDirectory() as tmp:
                ws = vfx.Workspace(Path(tmp), version="v01")
                path = ws.brief(1, idea=Idea(title="T", genre="g", description="d"),
                                house_rules="", style=STYLE, tokens={"ink": "#FFFFFF"},
                                frame=(1920, 1080), section=SECTION, effects=EFFECTS,
                                words={}, assets=[], worlds=vfx.world_symbols(SECTION, EFFECTS),
                                constraints=vfx.picture_constraints(script))
                return path.read_text(encoding="utf-8")
        text = brief(data)
        story = text.split("## Story constraints", 1)[1]
        self.assertIn("shot 1.1: picture:formula: shot 1.1: the section opens on F = ma", story)
        self.assertIn("REQUIRED: show the relation first as a worked case", story)
        self.assertNotIn("nested shells", text)
        data["picture"]["flags"].remove(formula)
        data["picture"]["kept"].append(dict(formula, kept="the beat needs it"))
        self.assertNotIn("F = ma", brief(data))


class Planner(unittest.TestCase):
    def test_the_planners_section_prompt_carries_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            idea = Idea(title="T", genre="g", description="d")
            bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": "",
                      "picture_check": vfx.picture_constraints(SCRIPT)}
            planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                     model="sonnet", verbose=False)
            seen = []

            def decide(name, prompt, schema):
                seen.append(prompt)
                return {"effects": [], "cuts": []}
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                planner._section_effects(SECTION, STYLE)
        self.assertIn("## Story constraints", seen[0])
        self.assertIn("nothing shows the lift", seen[0])


class Reviewer(AuthoringCase):
    def test_the_section_reviewer_and_author_are_held_to_them(self):
        harness_reviews = [review(8)]
        from tests.test_vfx_world import FakeEngine
        FakeEngine.reviews = harness_reviews
        planner = self.planner(revisions=0)
        planner.bundle["picture_check"] = vfx.picture_constraints(SCRIPT)
        self.author(planner)
        [first] = self.reviews()
        self.assertIn("## Story constraints", first["prompt"])
        self.assertIn("nothing shows the lift", first["prompt"])
        brief = (self.root / "vfx" / "v01" / "brief-01.md").read_text(encoding="utf-8")
        self.assertIn("## Story constraints", brief)


class Keyframe(ProjectCase):
    def test_the_keyframe_brief_carries_the_story_constraints(self):
        data = copy.deepcopy(KEYFRAME_SCRIPT)
        data["review"] = {"verdict": "consider", "flags": [
            {"n": 0, "section": 0, "criterion": "ending", "severity": "important",
             "problem": "the plane never lands where the story ends",
             "fix": "show the landing"}]}
        write_json(self.root / "script.json", data)
        self.run_stage()
        brief = (self.root / "keyframe" / keyframe.BRIEF_NAME).read_text()
        self.assertIn("## Story constraints", brief)
        self.assertIn("the plane never lands where the story ends", brief)
        self.assertIn("show the landing", brief)


# --------------------------------------------------------------------------- #
# AUTO-144: a process its picture-check says is shown static
# --------------------------------------------------------------------------- #

FROZEN = {"n": 2, "severity": "important", "kind": "picture:static-process",
          "problem": "picture:static-process: shot 1.2: the glide is said to lose height; "
                     "the picture is one fixed state",
          "fix": "show the change: a simulation, a step animation or a build the viewer "
                 "watches, one state per beat"}


def frozen_script():
    data = copy.deepcopy(SCRIPT)
    data["picture"]["flags"].append(copy.deepcopy(FROZEN))
    return data


def effect(name, kind, shots):
    return {"name": name, "type": kind, "shots": shots, "conveys": "c", "on_screen": "o",
            "brief": "b", "seconds": 4, "difficulty": 2, "hours": 1, "technique": [],
            "fallback": "f"}


class StaticCover(unittest.TestCase):
    def test_a_static_process_flag_is_a_picture_row_with_its_label(self):
        rows = vfx.picture_constraints(frozen_script())
        [row] = [r for r in rows if r["problem"].startswith("picture:static-process:")]
        self.assertEqual((row["source"], row["label"], row["severity"]),
                         ("picture", "1.2", "important"))

    def test_only_a_static_type_on_a_flagged_shot_is_listed(self):
        rows = vfx.picture_constraints(frozen_script())
        effects = [dict(effect("Card", "text-and-callout", ["1.2"]), id="open-card"),
                   dict(effect("Still", "photo-composite", ["1.1"]), id="open-still"),
                   dict(effect("Sim", "simulation", ["1.2"]), id="open-sim"),
                   dict(effect("Steps", "2d-diagram", ["1.2", "1.3"]), id="open-steps")]
        self.assertEqual(vfx.static_cover(effects, rows), [("open-card", "1.2")])
        # the unshown claim on 1.3 is not a static process
        self.assertEqual(vfx.static_cover(
            [dict(effect("C", "stock-treatment", ["1.3"]), id="x")], rows), [])
        self.assertEqual(vfx.static_cover(effects, []), [])
        # AUTO-145: an unneeded formula is not a static process either
        formula = [{"label": "1.1", "severity": "important", "source": "picture",
                    "problem": "picture:formula: shot 1.1: opens on F = ma", "fix": "f"}]
        self.assertEqual(vfx.static_cover(
            [dict(effect("C", "text-and-callout", ["1.1"]), id="x")], formula), [])
        self.assertEqual(vfx.STATIC_TYPES,
                         ("text-and-callout", "photo-composite", "stock-treatment"))


class PlanAgain(unittest.TestCase):
    def planner(self, root, answers, script=None):
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": "",
                  "picture_check": vfx.picture_constraints(script or frozen_script())}
        planner = vfx.VfxPlanner(bundle, Dossier(root, idea, at=root), vfx.Workspace(root),
                                 model="sonnet", verbose=False)
        asked = []

        def decide(name, prompt, schema):
            asked.append((name, prompt))
            return copy.deepcopy(answers.pop(0))
        return planner, asked, decide

    def test_a_static_cover_is_asked_once_more_then_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = {"effects": [effect("Card", "text-and-callout", ["1.1", "1.2"])],
                    "cuts": []}
            planner, asked, decide = self.planner(Path(tmp), [card, card])
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                got = planner._section_effects(SECTION, STYLE)
        self.assertEqual([n for n, _ in asked], ["plan-effects-open", "plan-effects-open-again"])
        again = asked[1][1]
        self.assertIn("1.2", again)
        self.assertIn("open-card", again)
        self.assertIn("watch the change", again)
        self.assertEqual(got["warnings"], ["open-card covers 1.2, a process its picture-check "
                                           "says is shown static"])

    def test_an_answer_that_lets_the_change_be_watched_settles_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = {"effects": [effect("Card", "text-and-callout", ["1.2"])], "cuts": []}
            sim = {"effects": [effect("Card", "simulation", ["1.2"])], "cuts": []}
            planner, asked, decide = self.planner(Path(tmp), [card, sim])
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                got = planner._section_effects(SECTION, STYLE)
        self.assertEqual(len(asked), 2)
        self.assertNotIn("warnings", got)
        self.assertEqual(got["effects"][0]["type"], "simulation")

    def test_a_first_answer_that_moves_asks_nothing_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            sim = {"effects": [effect("Sim", "simulation", ["1.2"])], "cuts": []}
            planner, asked, decide = self.planner(Path(tmp), [sim])
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                got = planner._section_effects(SECTION, STYLE)
        self.assertEqual([n for n, _ in asked], ["plan-effects-open"])
        self.assertNotIn("warnings", got)

    def test_a_film_with_no_process_shot_asks_nothing_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = {"effects": [effect("Card", "text-and-callout", ["1.2"])], "cuts": []}
            planner, asked, decide = self.planner(Path(tmp), [card], script=SCRIPT)
            with patch.object(planner, "_decide", decide), \
                    patch.object(planner, "vocabulary", lambda: ""):
                got = planner._section_effects(SECTION, STYLE)
        self.assertEqual(len(asked), 1)
        self.assertNotIn("warnings", got)

    def test_a_cached_plan_gains_its_warnings_without_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner, asked, decide = self.planner(Path(tmp), [])
            cached = [{"section": "Open", "seconds": 15, "cuts": [], "effects": [
                dict(effect("Card", "text-and-callout", ["1.2"]), id="open-card",
                     section="Open")]}]
            planner.dossier.save("11-effects", cached)
            with patch.object(planner, "_decide", decide):
                got = planner.effects({"sections": [SECTION]}, STYLE)
        self.assertEqual(asked, [])
        self.assertEqual(got[0]["warnings"], ["open-card covers 1.2, a process its "
                                              "picture-check says is shown static"])


def plain_data():
    """A small vfx.json as `yt vfx` writes it, before AUTO-144."""
    return {
        "idea": {"title": "T", "genre": "g", "description": "d"},
        "style": dict(STYLE), "tokens": {"ink": "#F2F5F7"},
        "sections": [{"section": "Open", "seconds": 15, "cuts": [], "effects": [
            dict(effect("Card", "text-and-callout", ["1.2"]), id="open-card",
                 section="Open")]}],
        "authoring": {"version": "v01", "dir": "vfx/v01", "versions": ["v01"],
                      "files": ["vfx/v01/01-open.scene.xml"], "author_bar": 7,
                      "revisions": 1, "scores": {"vfx/v01/01-open.scene.xml": 8}},
        "authored": [{"file": "vfx/v01/01-open.scene.xml", "symbols": ["world-open"],
                      "findings": [], "tool_calls": 3, "revisions": 0, "score": 8,
                      "cost_usd": 1.5, "rounds": []}],
        "scene": {"symbols": {"world-open": {"file": "vfx/v01/01-open.scene.xml"}},
                  "phases": {"open-card": "world-open"},
                  "by_label": {"1.2": {"file": "vfx/v01/01-open.scene.xml",
                                       "symbol": "world-open"}},
                  "dropped": {}, "files": ["vfx/v01/01-open.scene.xml"], "cost_usd": 1.5},
        "totals": vfx.summarise([{"effects": [effect("Card", "text-and-callout", ["1.2"])]}]),
        "reach": {"usage": None, "report": None, "audit": None,
                  "reason": "nothing was authored, so there is nothing to audit"},
        "plan": {"verdict": "finishable", "build_order": ["the card"]},
        "usage": {"cost_usd": 1.5},
    }


GOLDEN = Path(__file__).resolve().parent / "fixtures" / "vfx_md" / "plain.md"


def rendered(data):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dossier = Dossier(root, Idea(title="T", genre="g", description="d"), at=root)
        vfx.write_vfx(dossier, data)
        return (dossier.dir / "vfx.md").read_text(encoding="utf-8")


class Markdown(unittest.TestCase):
    def test_a_plan_without_warnings_renders_as_before(self):
        self.assertEqual(rendered(plain_data()), GOLDEN.read_text(encoding="utf-8"))

    def test_a_warning_is_printed_under_its_section(self):
        data = plain_data()
        data["sections"][0]["warnings"] = ["open-card covers 1.2, a process its "
                                           "picture-check says is shown static"]
        md = rendered(data)
        section = md.split("### Open (", 1)[1].split("#### Card", 1)[0]
        self.assertIn("warning: open-card covers 1.2, a process its picture-check says is "
                      "shown static", section)


if __name__ == "__main__":
    unittest.main()
