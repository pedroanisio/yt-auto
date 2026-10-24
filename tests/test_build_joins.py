"""build.py: the joins between the film's nodes become engine transitions (AUTO-142).

Before this every join in the film was a hard cut: the build placed an
<include> per world and a <layer> per picture and never wrote a
<transition>. Now placement is a pure step of its own (`layout`), the joins
are found on it (`joins_of`: which nodes meet, of which kind, how long a
window fits and which alignments the outgoing node's slack allows), the
editor chooses a transition or a cut for each, a model of another family
reviews the choice, and the build writes the chosen ones into scene.xml.
`--hard-cuts` keeps today's document byte for byte.

No model is called: the harness is a fake that answers by decision name.
"""

import copy
import io
import json
import struct
import tempfile
import threading
import unittest
import zlib
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from claude_engine import Usage
from ytstudio import build, cli, harness, vfx
from ytstudio.core import Idea
from ytstudio.harness import HarnessError
from ytstudio.scene_sdk import sdk
from ytstudio.script import Section, Shot


def png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80\x80\x80")) + chunk(b"IEND", b""))


def _doc(*symbols: tuple[str, float]) -> str:
    rows = "\n".join(
        f'    <symbol id="{sid}" duration="{seconds:g}">'
        f'<shape id="{sid}-s" shape="rect" width="100" height="100" fill="#FFFFFF"/></symbol>'
        for sid, seconds in symbols)
    return ('<?xml version="1.0"?>\n<scene version="1.1">\n'
            '  <project width="1920" height="1080" fps="30" duration="10"/>\n'
            f"  <symbols>\n{rows}\n  </symbols>\n  <composition/>\n</scene>\n")


#: Two worlds in the opening (a vfx plan cut), a third that opens the middle
#: section, an archival still after it and a shot nothing draws.
OPEN = _doc(("fx-1-1", 4), ("fx-1-2", 3))
MIDDLE = _doc(("fx-2-1", 6))
BY_LABEL = {"1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"},
            "1.2": {"file": "vfx/open.scene.xml", "symbol": "fx-1-2"},
            "2.1": {"file": "vfx/middle.scene.xml", "symbol": "fx-2-1"}}
DOSSIER = {"assets": [{"title": "an old photograph", "kind": "image", "licence": "CC0",
                       "credit_line": "an archive", "use_for": "the middle"}]}


def _shots():
    return [
        Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="a cell divides", narration="It splits.",
                 seconds=4, function="hook"),
            Shot(label="1.2", n=2, visual="the halves drift", narration="And drifts.",
                 seconds=3)]),
        Section(heading="Middle", shots=[
            Shot(label="2.1", n=3, visual="a lab bench", narration="In 1950 a bench.",
                 seconds=5, function="reveal"),
            Shot(label="2.2", n=4, visual="the old photograph", narration="This photo.",
                 seconds=2, asset="an old photograph"),
            Shot(label="2.3", n=5, visual="nothing drew this", narration="And then.",
                 seconds=3)])]


def _idea():
    return Idea.from_raw({"title": "A film", "genre": "explainer", "description": "p"})


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "vfx").mkdir()
        (self.root / "vfx" / "open.scene.xml").write_text(OPEN)
        (self.root / "vfx" / "middle.scene.xml").write_text(MIDDLE)
        (self.root / "downloads").mkdir()
        (self.root / "raw").mkdir()
        (self.root / "downloads" / "01-photo.png").write_bytes(png_1x1())
        (self.root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": "an old photograph", "url": "https://h.example/p.png", "status": "ok",
             "file": "downloads/01-photo.png", "licence": "CC0"}]))

    def builder(self, sections=None, by_label=None, **kw):
        kw.setdefault("audio", False)
        kw.setdefault("verbose", False)
        kw.setdefault("dossier", DOSSIER)
        return build.SceneBuilder(_idea(), sections or _shots(), self.root,
                                  vfx={"scene": {"by_label": by_label or BY_LABEL}}, **kw)


class Layout(_Project):
    def test_layout_then_build_is_build(self):
        data, manifest = self.builder().build()
        again, other = self.builder().build(self.builder().layout())
        self.assertEqual(data, again)
        self.assertEqual(manifest["findings"], other["findings"])

    def test_the_authored_lengths_are_read_once_per_world(self):
        layout = self.builder().layout()
        self.assertEqual(layout.authored, {("vfx/open.scene.xml", "fx-1-1"): 4.0,
                                           ("vfx/open.scene.xml", "fx-1-2"): 3.0,
                                           ("vfx/middle.scene.xml", "fx-2-1"): 6.0})

    def test_symbol_durations_is_what_the_include_copy_recorded_and_the_copy_is_unchanged(self):
        src = self.root / "vfx" / "open.scene.xml"
        first = self.root / "first.xml"
        info = build.cleaned_include(src, first)
        self.assertEqual(build.symbol_durations(src), info["durations"])
        self.assertEqual(build.symbol_durations(src), {"fx-1-1": 4.0, "fx-1-2": 3.0})
        # the copy is what it was: same bytes as an ElementTree round trip
        # of the authored file with nothing to drop
        root = ElementTree.parse(src).getroot()
        self.assertEqual(first.read_bytes(),
                         ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))
        self.assertEqual(build.symbol_durations(self.root / "missing.xml"), {})


class JoinsOf(_Project):
    def joins(self, **kw):
        return build.joins_of(self.builder(**kw).layout(), 30.0)

    def test_the_section_world_shot_and_placeholder_joins(self):
        joins = self.joins()
        self.assertEqual([(j.kind, j.from_id, j.to_id) for j in joins], [
            ("world", "shot-1.1", "shot-1.2"),
            ("section", "shot-1.2", "shot-2.1"),
            ("shot", "shot-2.1", "shot-2.2"),
            ("shot", "shot-2.2", "shot-2.3")])
        self.assertEqual([j.n for j in joins], [1, 2, 3, 4])
        self.assertEqual([j.at for j in joins], [4.0, 7.0, 12.0, 14.0])
        self.assertEqual((joins[1].from_label, joins[1].to_label), ("1.2", "2.1"))
        self.assertEqual(joins[1].to_function, "reveal")
        # a placeholder is a defect card, not a picture: a cut, not asked
        self.assertEqual([j.asked for j in joins], [True, True, True, False])

    def test_max_seconds_is_half_the_shorter_neighbour_capped(self):
        joins = self.joins()
        # 4 s and 3 s worlds: half of 3 is 1.5, over the cap
        self.assertEqual(joins[0].max_seconds, build.JOIN_MAX_S)
        # a 5 s world and a 2 s still: half of 2
        self.assertEqual(joins[2].max_seconds, 1.0)

    def test_an_include_with_no_slack_allows_only_end(self):
        joins = self.joins()
        # fx-1-1 is authored for 4 s and runs 4 s: TIMING_SLACK is all it has
        self.assertEqual(joins[0].alignments, ("end",))
        self.assertEqual(joins[1].alignments, ("end",))
        # fx-2-1 is authored for 6 s and runs 5 s: room for every window
        self.assertEqual(set(joins[2].alignments), set(build.JOIN_ALIGN))
        # a still holds: nothing bounds its handle
        self.assertEqual(set(joins[3].alignments), set(build.JOIN_ALIGN))

    def test_a_merged_run_has_no_join_inside_it(self):
        by_label = {"1.1": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"},
                    "1.2": {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}}
        sections = [Section(heading="Open", shots=[
            Shot(label="1.1", n=1, visual="v", narration="w", seconds=2),
            Shot(label="1.2", n=2, visual="v", narration="w", seconds=2)])]
        self.assertEqual(self.joins(sections=sections, by_label=by_label), [])

    def test_two_includes_of_one_symbol_are_never_a_join(self):
        layout = self.builder().layout()
        # the shape split_worlds reports: one symbol, two includes, touching
        hit = {"file": "vfx/open.scene.xml", "symbol": "fx-1-1"}
        layout.drawn["1.2"] = ("effect", hit)
        joins = build.joins_of(layout, 30.0)
        self.assertNotIn(("shot-1.1", "shot-1.2"), [(j.from_id, j.to_id) for j in joins])

    def test_a_gap_is_listed_and_never_asked_and_a_frame_still_meets(self):
        voice = {"takes": [
            {"label": "1.1", "at": 0.0, "seconds": 2.0},
            {"label": "1.2", "at": 2.3, "seconds": 2.0},
            {"label": "1.3", "at": 4.333, "seconds": 2.0}]}
        sections = [Section(heading="Open", shots=[
            Shot(label=f"1.{n}", n=n, visual="v", narration="w", seconds=2,
                 asset="an old photograph") for n in (1, 2, 3)])]
        joins = build.joins_of(self.builder(sections=sections, voice=voice).layout(), 30.0)
        self.assertEqual([j.kind for j in joins], ["gap", "shot"])
        self.assertFalse(joins[0].asked)
        self.assertTrue(joins[1].asked)


class Validate(_Project):
    def setUp(self):
        super().setUp()
        self.joins = build.joins_of(self.builder().layout(), 30.0)

    def answer(self, *rows):
        return {"joins": list(rows), "notes": ""}

    def ok(self):
        return [{"at_join": 1, "type": "cut", "duration": 0.5, "alignment": "center",
                 "why": "the world simply changes"},
                {"at_join": 2, "type": "dip-to-color", "duration": 0.8, "alignment": "end",
                 "why": "a breath before the new chapter"},
                {"at_join": 3, "type": "push", "duration": 0.6, "alignment": "center",
                 "direction": "left", "why": "the bench gives way to its photograph"}]

    def test_a_valid_answer_and_a_cut_pass(self):
        self.assertEqual(build.validate_joins(self.answer(*self.ok()), self.joins), [])

    def test_a_type_outside_the_menu_and_the_deferred_three_are_refused(self):
        self.assertNotIn("morph", build.JOIN_TYPES)
        for kind in ("morph", "luma", "shader", "star-wipe"):
            rows = self.ok()
            rows[2]["type"] = kind
            said = build.validate_joins(self.answer(*rows), self.joins)
            self.assertTrue(any("join 3" in s and kind in s for s in said), (kind, said))

    def test_a_duration_past_the_window_is_refused(self):
        rows = self.ok()
        rows[2]["duration"] = 1.1           # max is 1.0 next to a 2 s still
        said = build.validate_joins(self.answer(*rows), self.joins)
        self.assertTrue(any("join 3" in s and "1.1" in s for s in said), said)
        rows[2]["duration"] = 0.05
        self.assertTrue(build.validate_joins(self.answer(*rows), self.joins))

    def test_missing_and_duplicate_indices_are_refused(self):
        rows = self.ok()
        said = build.validate_joins(self.answer(*rows[:2]), self.joins)
        self.assertTrue(any("join 3" in s and "no entry" in s for s in said), said)
        said = build.validate_joins(self.answer(*rows, dict(rows[0])), self.joins)
        self.assertTrue(any("join 1" in s and "more than once" in s for s in said), said)
        # the placeholder join is not one of the joins asked
        said = build.validate_joins(self.answer(*rows, dict(rows[0], at_join=4)), self.joins)
        self.assertTrue(any("join 4" in s for s in said), said)

    def test_a_centred_window_past_the_includes_slack_is_refused(self):
        rows = self.ok()
        rows[0] = {"at_join": 1, "type": "crossfade", "duration": 0.6, "alignment": "center",
                   "why": "soft"}
        said = build.validate_joins(self.answer(*rows), self.joins)
        self.assertTrue(any("join 1" in s and "center" in s for s in said), said)
        # the slack bound itself, with the list widened by hand
        wide = copy.deepcopy(self.joins)
        wide[0].alignments = build.JOIN_ALIGN
        said = build.validate_joins(self.answer(*rows), wide)
        self.assertTrue(any("join 1" in s and "slack" in s for s in said), said)

    def test_a_direction_on_a_type_that_takes_none_is_refused(self):
        rows = self.ok()
        rows[1]["direction"] = "up"
        said = build.validate_joins(self.answer(*rows), self.joins)
        self.assertTrue(any("join 2" in s and "direction" in s for s in said), said)


def _all_cuts(prompt):
    return {"joins": [{"at_join": n, "type": "cut", "duration": 0.5, "alignment": "end",
                       "why": "a hard cut"} for n in (1, 2, 3)], "notes": ""}


def _chosen(prompt):
    return {"joins": [
        {"at_join": 1, "type": "crossfade", "duration": 0.4, "alignment": "end",
         "why": "the cell's halves become the next world"},
        {"at_join": 2, "type": "dip-to-color", "duration": 0.8, "alignment": "end",
         "why": "a breath before the new chapter"},
        {"at_join": 3, "type": "push", "duration": 0.6, "alignment": "center",
         "direction": "left", "why": "the bench gives way to its photograph"}], "notes": ""}


EDIT = {"coverage": [], "captions_off": [], "tail_seconds": 0.5, "notes": "", "blocking": []}
LOOK = {"tone_mapping": "none", "exposure": 0, "look": "none", "reason": "as authored"}


class Scripted:
    """build.Harness in these tests: answers each decision by its name from
    `answers` (a callable is given the prompt, an exception is raised) and
    shares the books it is given, as the real one does."""

    answers: dict = {}
    calls: list = []
    made: list = []

    def __init__(self, root, stage, *, model="sonnet", usage=None, decisions=None,
                 lock=None, **kw):
        self.model = model
        self.usage = usage if usage is not None else Usage()
        self.decisions = decisions if decisions is not None else []
        self.lock = lock if lock is not None else threading.Lock()
        Scripted.made.append({"model": model, "shared": usage is not None, **kw})

    def decide(self, name, prompt, schema):
        Scripted.calls.append((name, self.model))
        answer = Scripted.answers.get(name, {"edit": EDIT, "look": LOOK,
                                             "joins": _chosen, "joins-2": _chosen,
                                             "joins-revise": _chosen,
                                             "joins-review": {"accept": True,
                                                              "problems": []}}.get(name))
        if isinstance(answer, Exception):
            raise answer
        self.decisions.append(harness.Decision(name, False, "d", 0.0, model=self.model))
        return copy.deepcopy(answer(prompt) if callable(answer) else answer)

    def inspect(self, *a, **kw):
        return {"verdict": "none", "reason": ""}

    def report(self):
        return {"model": self.model, "by_model": self.usage.by_model,
                "decisions": [{"name": d.name, "model": d.model} for d in self.decisions]}


class _Run(_Project):
    def setUp(self):
        super().setUp()
        (self.root / "idea.json").write_text(json.dumps(
            {"title": "A film", "genre": "explainer", "description": "p"}))
        sections = []
        for section in _shots():
            sections.append({"heading": section.heading, "shots": [
                {"label": s.label, "n": s.n, "visual": s.visual, "narration": s.narration,
                 "seconds": s.seconds, "asset": s.asset, "function": s.function}
                for s in section.shots]})
        (self.root / "script.json").write_text(json.dumps({"sections": sections}))
        (self.root / "vfx.json").write_text(json.dumps(
            {"scene": {"by_label": BY_LABEL},
             "style": {"transitions": "joins dissolve on a thought, cut on a fact",
                       "camera_language": "slow push-ins"}}))
        (self.root / "dossier.json").write_text(json.dumps(DOSSIER))
        Scripted.answers, Scripted.calls, Scripted.made = {}, [], []

    def run_build(self, *extra, fake=Scripted):
        args = cli.build_parser().parse_args(
            ["build", "--project", str(self.root), "--no-audio", "--quiet", "--force", *extra])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(build, "Harness", fake), \
                mock.patch("sys.stdout", new=out), redirect_stderr(err):
            code = build.run(args)
        self.out, self.err = out.getvalue(), err.getvalue()
        return code

    def manifest(self):
        return json.loads((self.root / "build.json").read_text())

    def names(self):
        return [name for name, _ in Scripted.calls]


class Flow(_Run):
    def test_the_editor_chooses_the_reviewer_accepts_and_the_scene_has_them(self):
        self.assertEqual(self.run_build(), 0, self.err)
        self.assertEqual(self.names(), ["edit", "look", "joins", "joins-review"])
        models = dict(Scripted.calls)
        self.assertEqual((models["joins"], models["joins-review"]),
                         ("sonnet", vfx.REVIEWER_MODEL))
        manifest = self.manifest()
        rows = manifest["joins"]
        self.assertEqual([r["type"] for r in rows], ["crossfade", "dip-to-color", "push", "cut"])
        self.assertEqual([r["review"] for r in rows],
                         ["accepted", "accepted", "accepted", "not-asked"])
        for key in ("n", "at", "kind", "from", "to", "from_label", "to_label", "type",
                    "duration", "alignment", "why", "review"):
            self.assertIn(key, rows[0])
        self.assertEqual(manifest["joins_review"]["model"], vfx.REVIEWER_MODEL)
        self.assertTrue(manifest["joins_review"]["accept"])
        root = ElementTree.fromstring((self.root / "scene.xml").read_bytes())
        self.assertEqual(len(root.findall("composition/transition")), 3)
        self.assertIn("4 joins: 1 crossfade, 1 dip-to-color, 1 push, 1 cut", self.out)
        self.assertIn(f"reviewed by {vfx.REVIEWER_MODEL}: accepted", self.out)

    def test_the_prompt_carries_the_style_prose_and_each_asked_join(self):
        seen = {}

        def capture(prompt):
            seen["prompt"] = prompt
            return _chosen(prompt)
        Scripted.answers = {"joins": capture}
        self.assertEqual(self.run_build(), 0, self.err)
        prompt = seen["prompt"]
        self.assertIn("joins dissolve on a thought, cut on a fact", prompt)
        self.assertIn("slow push-ins", prompt)
        self.assertIn("In 1950 a bench.", prompt)
        self.assertIn("narrative purpose", prompt)
        self.assertNotIn('"at_join": 4', prompt)

    def test_an_invalid_answer_is_asked_again_once(self):
        bad = _chosen("")
        bad["joins"][2]["duration"] = 3.0
        Scripted.answers = {"joins": bad}
        self.assertEqual(self.run_build(), 0, self.err)
        self.assertEqual(self.names(), ["edit", "look", "joins", "joins-2", "joins-review"])
        self.assertEqual(self.manifest()["joins"][2]["type"], "push")

    def test_two_invalid_answers_give_hard_cuts_and_a_warning(self):
        bad = _chosen("")
        bad["joins"][2]["duration"] = 3.0
        Scripted.answers = {"joins": bad, "joins-2": bad}
        self.assertEqual(self.run_build(), 0, self.err)
        self.assertNotIn("joins-review", self.names())
        manifest = self.manifest()
        self.assertEqual({r["type"] for r in manifest["joins"]}, {"cut"})
        self.assertEqual([r["review"] for r in manifest["joins"]][:3], ["fallback"] * 3)
        self.assertTrue(any(f.startswith("warning: the editor's joins did not hold")
                            and "hard cuts" in f for f in manifest["findings"]))
        root = ElementTree.fromstring((self.root / "scene.xml").read_bytes())
        self.assertEqual(root.findall("composition/transition"), [])

    def test_a_reject_gets_one_revision_and_an_open_problem_is_a_warning(self):
        def revised(prompt):
            self.assertIn("too slow for a fact", prompt)
            answer = _chosen(prompt)
            answer["joins"][1] = {"at_join": 2, "type": "cut", "duration": 0.5,
                                  "alignment": "end", "why": "a fact lands hard"}
            return answer
        Scripted.answers = {"joins-review": {"accept": False, "problems": [
            {"at_join": 2, "problem": "too slow for a fact", "fix": "cut"},
            {"at_join": 3, "problem": "the push fights the camera", "fix": "crossfade"}]},
            "joins-revise": revised}
        self.assertEqual(self.run_build(), 0, self.err)
        self.assertEqual(self.names().count("joins-revise"), 1)
        self.assertEqual(self.names().count("joins-review"), 1)
        manifest = self.manifest()
        rows = manifest["joins"]
        self.assertEqual([r["review"] for r in rows[:3]], ["accepted", "revised", "unresolved"])
        self.assertEqual(rows[1]["type"], "cut")
        warned = [f for f in manifest["findings"] if "the push fights the camera" in f]
        self.assertEqual(len(warned), 1, manifest["findings"])
        self.assertIn("join 3", warned[0])
        self.assertIn("2.1", warned[0])
        self.assertFalse(manifest["joins_review"]["accept"])
        self.assertEqual(len(manifest["joins_review"]["problems"]), 2)

    def test_a_failed_review_applies_the_editors_joins_unresolved(self):
        Scripted.answers = {"joins-review": HarnessError("[build] joins-review: over budget")}
        self.assertEqual(self.run_build(), 0, self.err)
        manifest = self.manifest()
        self.assertEqual([r["type"] for r in manifest["joins"]][:3],
                         ["crossfade", "dip-to-color", "push"])
        self.assertEqual({r["review"] for r in manifest["joins"][:3]}, {"unresolved"})
        self.assertTrue(any(f.startswith("warning:") and "joins review" in f
                            and "over budget" in f for f in manifest["findings"]))

    def test_a_failed_editor_gives_hard_cuts(self):
        Scripted.answers = {"joins": HarnessError("[build] joins: the model is down")}
        self.assertEqual(self.run_build(), 0, self.err)
        manifest = self.manifest()
        self.assertEqual({r["type"] for r in manifest["joins"]}, {"cut"})
        self.assertTrue(any("did not hold" in f and "the model is down" in f
                            for f in manifest["findings"]))

    def test_the_reviewer_is_built_on_the_authors_books(self):
        self.assertEqual(self.run_build(), 0, self.err)
        author, reviewer = Scripted.made
        self.assertEqual((author["model"], reviewer["model"]), ("sonnet", vfx.REVIEWER_MODEL))
        self.assertTrue(reviewer["shared"])
        self.assertEqual(reviewer["budget_usd"], vfx.REVIEW_USD)
        self.assertEqual(reviewer["role"], build.JOIN_REVIEW_ROLE)
        self.assertNotIn("budget_usd", author)


class Refused(_Run):
    def test_the_authors_own_family_is_refused_before_any_call(self):
        self.assertEqual(self.run_build("--model", "opus", "--reviewer-model", "opus"), 2)
        self.assertEqual(Scripted.calls, [])
        self.assertEqual(Scripted.made, [])
        self.assertIn("does not grade its own work", self.err)

    def test_the_pool_is_refused_with_the_builds_message(self):
        for value in ("pool", "sdk:zai/glm-5.3"):
            self.assertEqual(self.run_build("--reviewer-model", value), 2)
            self.assertEqual(Scripted.made, [])
            self.assertIn("the joins review of `yt build` takes a claude or codex model; "
                          "the gateway pool is not wired to this stage", self.err)

    def test_hard_cuts_asks_no_reviewer_so_refuses_nothing(self):
        self.assertEqual(self.run_build("--model", "opus", "--hard-cuts"), 0, self.err)


class Accounts(_Run):
    """The real Harness on a fake engine: both models are billed on one record."""

    def test_by_model_lists_the_author_and_the_reviewer(self):
        asked = []

        class Engine:
            def __init__(self, config):
                self.config = config
                self.usage = Usage()
                self.usage.calls = 1
                self.usage.cost_usd = 0.01
                self.usage.by_model = {config.model: {"calls": 1, "cost_usd": 0.01}}

            def ask_json(self, prompt, schema, system=None):
                asked.append((self.config, schema))
                props = schema.get("properties", {})
                if "coverage" in props:
                    return EDIT
                if "tone_mapping" in props:
                    return LOOK
                if "accept" in props:
                    return {"accept": True, "problems": []}
                return _chosen(prompt)
        with mock.patch.object(harness, "ENGINE", Engine), \
                mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("YT_GATEWAY", None)
            self.assertEqual(self.run_build(fake=harness.Harness), 0, self.err)
            report = self.manifest()["engine"]
            self.assertEqual(set(report["by_model"]), {"sonnet", vfx.REVIEWER_MODEL})
            self.assertEqual([d["name"] for d in report["decisions"]],
                             ["edit", "look", "joins", "joins-review"])
            self.assertEqual([d["model"] for d in report["decisions"]][-1], vfx.REVIEWER_MODEL)
            default = {name: config for (config, _), name in
                       zip(asked, ["edit", "look", "joins", "joins-review"])}
            decided = {p.stem: json.loads(p.read_text())["digest"]
                       for p in (self.root / "decisions" / "build").glob("*.json")}
            # --hard-cuts: the two questions build always asked, asked as
            # they always were (same engine config, same digest), and nothing else
            asked.clear()
            self.assertEqual(self.run_build("--hard-cuts", "--fresh", fake=harness.Harness),
                             0, self.err)
            self.assertEqual(len(asked), 2)
            self.assertEqual(asked[0][0], default["edit"])
            self.assertEqual(asked[1][0], default["look"])
            report = self.manifest()["engine"]
            self.assertEqual([d["name"] for d in report["decisions"]], ["edit", "look"])
            again = {p.stem: json.loads(p.read_text())["digest"]
                     for p in (self.root / "decisions" / "build").glob("*.json")}
            self.assertEqual(again["edit"], decided["edit"])
            self.assertEqual(again["look"], decided["look"])


def _schematron(data: bytes) -> list[str]:
    """C20-C24 and V3 of sr-core's scene-render.sch, read with ElementTree:
    the SDK's own level 2 needs lxml, which this repository does not take."""
    root = ElementTree.fromstring(data)
    out = []
    for parent in root.iter():
        for t in parent.findall("transition"):
            ids = [c.get("id") for c in parent]
            if not (t.get("from") or t.get("to") or parent.tag == "segment"):
                out.append("C20")
            if t.get("type") == "shader" and not t.get("shader"):
                out.append("C21")
            if t.get("type") == "luma" and not t.get("matte"):
                out.append("C22")
            if t.get("from") and ids.count(t.get("from")) != 1:
                out.append("C23")
            if t.get("to") and ids.count(t.get("to")) != 1:
                out.append("C24")
    if root.get("version") == "1.0" and root.find(".//transition") is not None:
        out.append("V3")
    return out


class Document(_Project):
    def chosen(self):
        return [{"n": 1, "type": "crossfade", "duration": 0.4, "alignment": "end",
                 "why": "a", "review": "accepted"},
                {"n": 2, "type": "dip-to-color", "duration": 0.8, "alignment": "end",
                 "why": "b", "review": "accepted"},
                {"n": 3, "type": "cut", "duration": 0.5, "alignment": "end",
                 "why": "c", "review": "accepted"}]

    def test_the_chosen_transitions_are_written_between_the_nodes(self):
        b = self.builder()
        layout = b.layout()
        data, manifest = b.build(layout, self.chosen())
        root = ElementTree.fromstring(data)
        found = root.findall("composition/transition")
        self.assertEqual([(t.get("from"), t.get("to"), t.get("type")) for t in found],
                         [("shot-1.1", "shot-1.2", "crossfade"),
                          ("shot-1.2", "shot-2.1", "dip-to-color")])
        self.assertEqual([t.get("id") for t in found], ["join-1", "join-2"])
        self.assertEqual((found[0].get("duration"), found[0].get("alignment")), ("0.4", "end"))
        self.assertEqual(found[1].get("color").lower(), b.background.lower())
        self.assertIsNone(found[0].get("color"))
        # the nodes keep their times: the engine extends the handles
        includes = {i.get("id"): (i.get("start"), i.get("end"))
                    for i in root.findall("composition/include")}
        plain, _ = self.builder().build()
        before = {i.get("id"): (i.get("start"), i.get("end"))
                  for i in ElementTree.fromstring(plain).findall("composition/include")}
        self.assertEqual(includes, before)
        # it reads back, and holds C20-C24 and V3
        errors = [str(f) for f in sdk().read(data).check() if str(f).startswith("error")]
        self.assertEqual(errors, [])
        self.assertEqual(_schematron(data), [])
        self.assertGreaterEqual(tuple(int(x) for x in manifest["scene"]["version"].split(".")),
                                (1, 1))
        rows = manifest["joins"]
        self.assertEqual([r["type"] for r in rows], ["crossfade", "dip-to-color", "cut", "cut"])
        self.assertEqual(rows[3]["review"], "not-asked")
        self.assertIn("placeholder", rows[3]["why"])

    def test_a_cut_writes_no_element_and_all_cuts_are_todays_document(self):
        cuts = [dict(row, type="cut") for row in self.chosen()]
        data, manifest = self.builder().build(self.builder().layout(), cuts)
        today, plain = self.builder().build()
        self.assertEqual(data, today)
        self.assertEqual(manifest["scene"]["version"], plain["scene"]["version"])
        self.assertEqual({r["review"] for r in plain["joins"]}, {"off"})
        self.assertEqual({r["type"] for r in plain["joins"]}, {"cut"})


class HardCuts(_Run):
    def test_hard_cuts_is_todays_scene_byte_for_byte(self):
        self.assertEqual(self.run_build("--hard-cuts"), 0, self.err)
        self.assertEqual(self.names(), ["edit", "look"])
        data = (self.root / "scene.xml").read_bytes()
        builder = build.SceneBuilder(
            _idea(), _shots(), self.root, vfx={"scene": {"by_label": BY_LABEL}},
            dossier=DOSSIER, audio=False, verbose=False, edit=EDIT)
        today, _ = builder.build()
        self.assertEqual(data, today)
        manifest = self.manifest()
        self.assertEqual({r["type"] for r in manifest["joins"]}, {"cut"})
        self.assertEqual({r["review"] for r in manifest["joins"]}, {"off"})
        self.assertNotIn("joins_review", manifest)


class YtRun(unittest.TestCase):
    def args(self, *argv):
        return cli.build_parser().parse_args(["run", "--title", "T", *argv])

    def walk(self, *argv):
        err = io.StringIO()
        with mock.patch.object(cli.workspace, "resolve",
                               side_effect=AssertionError("passed the check")), \
                redirect_stderr(err):
            try:
                code = cli._run_pipeline(self.args(*argv))
            except AssertionError as exc:
                self.assertEqual(str(exc), "passed the check")
                code = None
        return code, err.getvalue()

    def test_the_builds_review_refuses_the_authors_family_before_any_stage(self):
        code, said = self.walk("--model", "opus", "--skip", "keyframe,vfx", "--dry-run")
        self.assertEqual(code, 2)
        code, said = self.walk("--model", "opus", "--skip", "keyframe,vfx,script", "--dry-run")
        self.assertEqual(code, 2, said)
        self.assertIn("does not grade its own work", said)
        code, _ = self.walk("--model", "opus", "--skip", "keyframe,vfx,script,build",
                            "--dry-run")
        self.assertIsNone(code)

    def test_quick_hands_build_hard_cuts_and_is_not_refused(self):
        extra = cli.run_extras(self.args("--quick"), redo=set())
        self.assertEqual(extra["build"], ["--hard-cuts"])
        code, _ = self.walk("--model", "opus", "--skip", "keyframe,vfx", "--quick")
        self.assertIsNone(code)

    def test_build_gets_the_cli_reviewer_and_not_the_pool(self):
        extra = cli.run_extras(self.args("--reviewer-model", "haiku",
                                         "--review-budget-usd", "1.5"), redo=set())
        self.assertEqual(extra["build"],
                         ["--reviewer-model", "haiku", "--review-budget-usd", "1.5"])
        ns = cli.build_parser().parse_args(cli.stage_argv("build", ["--model", "sonnet"], extra))
        self.assertEqual((ns.reviewer_model, ns.review_budget_usd, ns.hard_cuts),
                         ("haiku", 1.5, False))
        extra = cli.run_extras(self.args("--reviewer-model", "sdk:zai/glm-5.3"), redo=set())
        self.assertNotIn("--reviewer-model", extra["build"])
        self.assertEqual(cli.run_extras(self.args(), redo=set())["build"], [])

    def test_a_pool_reviewer_leaves_build_on_the_default_checked_by_family(self):
        code, said = self.walk("--model", "opus", "--reviewer-model", "pool",
                               "--skip", "keyframe,vfx")
        self.assertEqual(code, 2)
        self.assertIn("build", said)


if __name__ == "__main__":
    unittest.main()
