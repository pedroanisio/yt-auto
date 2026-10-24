"""AUTO-146: `yt styles --rich` writes down the film's identity, and a
reviewer of another family swap-tests it.

The treatment pool says which looks can carry the message; nothing said what
makes this film's look its own, so a polished, generic film could reach
READY. With --rich the choice now carries `identity` (atmosphere,
personality, motif, signature, belongs_because). With `critique` among the
deliberation stages a second harness, on --reviewer-model and never the
author's family, judges it: could it be moved to another topic unchanged?
A failing identity goes back to the author once, and the outcome is
recorded as pass, weak or not_evidenced, never a pass nobody gave.

No model is called: harness.ENGINE is a fake that answers by schema and
records which model each question went to.
"""

import ast
import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from claude_engine import Usage
from ytstudio import cli, harness, keyframe, styles, vfx
from ytstudio.core import Idea

ROOT = Path(__file__).resolve().parent.parent

IDENTITY = {"atmosphere": "the held breath before a load gives way",
            "personality": "forensic and patient",
            "motif": "the hairline crack in the gusset plate, traced in chalk",
            "signature": "every section opens on the crack, a little longer each time",
            "belongs_because": "the message is that the failure was visible years before"}

CHOICE = {
    "message": "the bridge told everyone it would fall", "demands": ["a crack growing"],
    "combination": "c", "avoid": [], "thumbnail": "t", "identity": IDENTITY,
    "styles": [
        {"name": "Diagram", "fit": 9, "why": "w", "shows_well": "s", "shots": ["a"],
         "effort": "low", "pitfalls": "p", "tools": [], "time_estimate": "1 week",
         "palette": "background (#101010)", "typography": "Inter", "motion": "slow",
         "sound": "creaks", "references": []},
    ],
}

PASS = {"swappable": False, "swap_target": "", "fit_to_subject": 8, "distinct": 7.5,
        "objection": "the motif is the subject's own"}
SWAPPABLE = {"swappable": True, "swap_target": "any engineering disaster",
             "fit_to_subject": 7, "distinct": 4, "objection": "chalk on steel suits any failure",
             "revised": {"motif": "the 1967 inspector's own sketch"}}
REVISED = dict(IDENTITY, motif="the inspector's 1967 sketch of the gusset, redrawn live")

#: style_schema(False) as it was before AUTO-146, frozen: the non-rich schema
#: (and so every non-rich cache key) does not move.
PLAIN_SCHEMA = json.loads(
    '{"properties": {"avoid": {"items": {"properties": {"name": {"type": "string"}, '
    '"why": {"type": "string"}}, "required": ["name", "why"], "type": "object"}, '
    '"type": "array"}, "combination": {"type": "string"}, "demands": {"items": '
    '{"type": "string"}, "type": "array"}, "message": {"type": "string"}, "styles": '
    '{"items": {"properties": {"effort": {"enum": ["low", "medium", "high"], "type": '
    '"string"}, "fit": {"type": "number"}, "name": {"type": "string"}, "pitfalls": '
    '{"type": "string"}, "shots": {"items": {"type": "string"}, "type": "array"}, '
    '"shows_well": {"type": "string"}, "why": {"type": "string"}}, "required": ["name", '
    '"fit", "why", "shows_well", "shots", "effort", "pitfalls"], "type": "object"}, '
    '"type": "array"}}, "required": ["message", "demands", "styles", "combination", '
    '"avoid"], "type": "object"}')


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Fake:
    """Stands in for ClaudeEngine. Answers by schema; the reviewer's answers
    come from `judgements` in order (a dict, or an exception to raise)."""

    calls: list = []
    judgements: list = []
    revise: dict = REVISED
    built = 0

    def __init__(self, config):
        Fake.built += 1
        self.config = config
        self.usage = Usage()
        self.usage.cost_usd, self.usage.calls = 0.01, 1

    def ask_json(self, prompt, schema, system=None):
        Fake.calls.append({"model": self.config.model, "schema": schema,
                           "prompt": prompt, "system": system})
        if schema is styles.IDENTITY_SCHEMA:
            answer = Fake.judgements.pop(0) if Fake.judgements else PASS
            if isinstance(answer, Exception):
                raise answer
            return copy.deepcopy(answer)
        if schema is styles.IDENTITY_REVISE_SCHEMA:
            return copy.deepcopy(Fake.revise)
        if schema is styles.CRITIQUE_SCHEMA:
            return {"verdicts": [], "overlooked": []}
        if schema is styles.SWEEP_SCHEMA:
            return {"scores": [{"name": "Diagram", "fit": 9, "note": "n"}]}
        return copy.deepcopy(CHOICE)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        saved = harness.ENGINE
        self.addCleanup(setattr, harness, "ENGINE", saved)
        harness.ENGINE = Fake
        Fake.calls, Fake.judgements, Fake.revise, Fake.built = [], [], REVISED, 0
        self.idea = Idea(title="Why the bridge fell", genre="explainer", description="d")

    def asked(self, schema):
        return [c for c in Fake.calls if c["schema"] is schema]


class Schema(unittest.TestCase):
    def test_the_rich_schema_requires_the_identity_and_its_five_fields(self):
        schema = styles.style_schema(True)
        self.assertIn("identity", schema["required"])
        ident = schema["properties"]["identity"]
        self.assertEqual(ident["type"], "object")
        self.assertEqual(ident["required"], list(styles.IDENTITY_FIELDS))
        self.assertEqual(list(styles.IDENTITY_FIELDS),
                         ["atmosphere", "personality", "motif", "signature",
                          "belongs_because"])
        for key in styles.IDENTITY_FIELDS:
            self.assertEqual(ident["properties"][key], {"type": "string"})

    def test_the_plain_schema_is_unchanged(self):
        self.assertEqual(styles.style_schema(False), PLAIN_SCHEMA)

    def test_the_review_schema(self):
        s = styles.IDENTITY_SCHEMA
        self.assertEqual(sorted(s["required"]),
                         sorted(["swappable", "swap_target", "fit_to_subject", "distinct",
                                 "objection"]))
        self.assertNotIn("revised", s["required"])
        self.assertEqual(s["properties"]["swappable"], {"type": "boolean"})
        self.assertEqual(sorted(s["properties"]["revised"]["properties"]),
                         sorted(styles.IDENTITY_FIELDS))

    def test_the_bar_is_a_starting_value(self):
        self.assertEqual(styles.IDENTITY_BAR, 6.0)
        src = (ROOT / "ytstudio" / "styles.py").read_text(encoding="utf-8")
        before = src.split("IDENTITY_BAR =", 1)[0].rsplit("\n\n", 1)[-1]
        self.assertIn("NO STANDARD", before)


class Prompt(unittest.TestCase):
    idea = Idea(title="Why the bridge fell", genre="explainer", description="d")

    def prompt(self, rich):
        return styles.build_prompt(self.idea, count=2, pool=["- Diagram: d"], rich=rich,
                                   constraints=["solo"], context="ctx", house="house",
                                   stack="stack")

    def test_the_rich_prompt_asks_for_the_identity_and_the_swap_test(self):
        text = self.prompt(True)
        self.assertIn("swap test", text)
        for key in styles.IDENTITY_FIELDS:
            self.assertIn(key, text)

    def test_the_plain_prompt_is_byte_identical_to_before(self):
        self.assertEqual(sha(self.prompt(False)), "59df57496553e766")
        self.assertNotIn("swap test", self.prompt(False))

    def test_the_rich_prompt_differs_only_by_the_identity_paragraph(self):
        rich = self.prompt(True)
        paragraph = next(p for p in rich.split("\n\n") if "swap test" in p)
        self.assertEqual(sha(rich.replace("\n\n" + paragraph, "", 1)), "a9aebffdc82e3661")

    def test_no_look_is_hardcoded_in_the_identity_paragraph(self):
        # invariant 2: the identity is the model's, per film
        rich = self.prompt(True)
        paragraph = next(p for p in rich.split("\n\n") if "swap test" in p).lower()
        for word in ("#", "inter", "lora", "noir", "neon", "pastel", "minimal"):
            self.assertNotIn(word, paragraph)


class Review(EngineCase):
    def director(self, **kw):
        kw.setdefault("rich", True)
        kw.setdefault("modes", {"critique"})
        return styles.Director(self.idea, root=self.root, verbose=False, count=1,
                               model="sonnet", reviewer_model="opus", **kw)

    def test_the_identity_is_judged_by_the_reviewer_never_the_author(self):
        data, picks = self.director().run([("Diagram", "d")])
        judged = self.asked(styles.IDENTITY_SCHEMA)
        self.assertEqual([c["model"] for c in judged], ["opus"])
        authored = [c for c in Fake.calls if c["schema"] is not styles.IDENTITY_SCHEMA]
        self.assertTrue(authored)
        self.assertEqual({c["model"] for c in authored}, {"sonnet"})
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "pass")
        self.assertEqual(review["scores"], {"fit_to_subject": 8.0, "distinct": 7.5})
        self.assertEqual(review["swap_target"], "")
        self.assertEqual(data["identity"]["motif"], IDENTITY["motif"])

    def test_the_reviewer_is_shown_the_identity_and_the_primary_treatment(self):
        self.director().run([("Diagram", "d")])
        prompt = self.asked(styles.IDENTITY_SCHEMA)[0]["prompt"]
        for value in IDENTITY.values():
            self.assertIn(value, prompt)
        self.assertIn("Diagram", prompt)
        self.assertIn(CHOICE["message"], prompt)
        self.assertIn("swap", prompt)

    def test_the_reviewer_reads_no_revision_notes(self):
        notes = self.root / harness.FEEDBACK_DIR
        notes.mkdir(parents=True)
        (notes / "styles.md").write_text("- make it purple\n", encoding="utf-8")
        self.director().run([("Diagram", "d")])
        self.assertNotIn("purple", self.asked(styles.IDENTITY_SCHEMA)[0]["system"])

    def test_without_critique_or_rich_nothing_is_judged(self):
        for kw in ({"modes": set()}, {"rich": False}, {"modes": {"sweep"}}):
            Fake.calls = []
            data, _ = self.director(**kw).run([("Diagram", "d")])
            self.assertEqual(self.asked(styles.IDENTITY_SCHEMA), [], kw)
            self.assertNotIn("review", data.get("identity") or {}, kw)

    def test_a_choice_with_no_identity_asks_no_reviewer(self):
        bare = copy.deepcopy(CHOICE)
        bare.pop("identity")
        with mock.patch.object(styles.Director, "_ask",
                               lambda self_, name, prompt, schema: copy.deepcopy(
                                   {"verdicts": [], "overlooked": []}
                                   if schema is styles.CRITIQUE_SCHEMA else bare)):
            data, _ = self.director().run([("Diagram", "d")])
        self.assertEqual(Fake.calls, [])
        self.assertNotIn("identity", data)

    def test_a_swappable_identity_is_revised_once_and_judged_again(self):
        Fake.judgements = [SWAPPABLE, PASS]
        data, _ = self.director().run([("Diagram", "d")])
        revise = self.asked(styles.IDENTITY_REVISE_SCHEMA)
        self.assertEqual(len(revise), 1)
        self.assertEqual(revise[0]["model"], "sonnet")
        self.assertIn(SWAPPABLE["objection"], revise[0]["prompt"])
        self.assertIn(SWAPPABLE["swap_target"], revise[0]["prompt"])
        self.assertEqual([c["model"] for c in self.asked(styles.IDENTITY_SCHEMA)],
                         ["opus", "opus"])
        self.assertIn(REVISED["motif"], self.asked(styles.IDENTITY_SCHEMA)[1]["prompt"])
        self.assertEqual(data["identity"]["motif"], REVISED["motif"])
        self.assertEqual(data["identity"]["review"]["verdict"], "pass")
        self.assertTrue(data["identity"]["review"]["revised"])

    def test_a_low_score_alone_sends_it_back(self):
        Fake.judgements = [dict(PASS, distinct=5.9), PASS]
        self.director().run([("Diagram", "d")])
        self.assertEqual(len(self.asked(styles.IDENTITY_REVISE_SCHEMA)), 1)

    def test_still_failing_after_the_revise_is_weak(self):
        Fake.judgements = [SWAPPABLE, dict(SWAPPABLE, objection="still any failure")]
        data, _ = self.director().run([("Diagram", "d")])
        self.assertEqual(len(self.asked(styles.IDENTITY_REVISE_SCHEMA)), 1)
        self.assertEqual(len(self.asked(styles.IDENTITY_SCHEMA)), 2)
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "weak")
        self.assertEqual(review["objection"], "still any failure")
        self.assertEqual(review["scores"], {"fit_to_subject": 7.0, "distinct": 4.0})

    def test_a_reviewer_that_never_answers_the_schema_is_not_evidenced(self):
        # every attempt misses a required field: Harness.decide asks twice, then raises
        Fake.judgements = [{"swappable": False}, {"swappable": False}]
        data, _ = self.director().run([("Diagram", "d")])
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "not_evidenced")
        self.assertIn("error", review)
        self.assertNotIn("scores", review)
        self.assertNotIn("swap_target", review)
        self.assertEqual(self.asked(styles.IDENTITY_REVISE_SCHEMA), [])

    def test_a_reviewer_that_fails_on_the_second_judgement_is_not_evidenced(self):
        Fake.judgements = [SWAPPABLE, harness.ClaudeError("the model is down")]
        data, _ = self.director().run([("Diagram", "d")])
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "not_evidenced")
        self.assertIn("the model is down", review["error"])
        self.assertNotIn("scores", review)

    def test_scores_that_are_not_numbers_are_not_evidenced(self):
        Fake.judgements = [dict(PASS, distinct="high"), dict(PASS, distinct="high")]
        data, _ = self.director().run([("Diagram", "d")])
        self.assertEqual(data["identity"]["review"]["verdict"], "not_evidenced")

    def off_scale(self, scale):
        Fake.judgements = [dict(PASS, **scale), dict(PASS, **scale)]
        data, _ = self.director().run([("Diagram", "d")])
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "not_evidenced")
        self.assertIn("0-10", review["error"])
        self.assertNotIn("scores", review)

    def test_scores_out_of_a_hundred_are_not_evidenced(self):
        # a reviewer answering on another scale would otherwise pass on nonsense
        self.off_scale({"fit_to_subject": 45, "distinct": 50})

    def test_a_negative_score_is_not_evidenced(self):
        self.off_scale({"fit_to_subject": 8, "distinct": -1})

    def test_a_failed_revise_keeps_the_first_identity_and_its_weak_verdict(self):
        Fake.judgements = [SWAPPABLE]
        Fake.revise = {"atmosphere": "only one field"}       # misses four, twice
        data, _ = self.director().run([("Diagram", "d")])
        self.assertEqual(data["identity"]["motif"], IDENTITY["motif"])
        review = data["identity"]["review"]
        self.assertEqual(review["verdict"], "weak")
        self.assertEqual(review["objection"], SWAPPABLE["objection"])
        self.assertEqual(review["scores"], {"fit_to_subject": 7.0, "distinct": 4.0})
        self.assertEqual(len(self.asked(styles.IDENTITY_SCHEMA)), 1)

    def test_the_reviewers_spend_is_in_the_stages_usage_and_report(self):
        director = self.director()
        data, _ = director.run([("Diagram", "d")])
        self.assertEqual(director.usage.calls, len(Fake.calls))
        names = [d["name"] for d in data["engine"]["reviewer"]["decisions"]]
        self.assertEqual(names, ["identity"])
        self.assertEqual(data["engine"]["reviewer"]["model"], "opus")


class Command(EngineCase):
    def setUp(self):
        super().setUp()
        self.film = self.root / "work" / "bridge"
        self.film.mkdir(parents=True)
        (self.film / "idea.json").write_text(
            '{"title": "Why the bridge fell", "genre": "g", "description": "d"}')

    def run_stage(self, *flags):
        args = cli.build_parser().parse_args(
            ["styles", "--project", str(self.film), "--work", str(self.root / "work"),
             "--quiet", "--count", "1", "--only", "Diagram", *flags])
        with \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = styles.run(args)
        return code, out.getvalue(), err.getvalue()

    def test_the_flag_defaults_to_the_house_reviewer(self):
        ns = cli.build_parser().parse_args(["styles"])
        self.assertEqual(ns.reviewer_model, styles.REVIEWER_MODEL)

    def test_the_authors_family_as_reviewer_is_refused_before_any_call(self):
        code, _, err = self.run_stage("--rich", "--deliberate", "critique",
                                      "--model", "opus", "--reviewer-model", "opus")
        self.assertEqual(code, 2)
        self.assertIn("different model", err)
        self.assertEqual(Fake.built, 0)
        self.assertFalse((self.film / "styles.json").exists())

    def test_a_pool_or_sdk_reviewer_is_refused_with_or_without_the_gateway(self):
        for value in ("pool", "sdk:x", "sdk:zai/glm-5.3"):
            for gateway in (None, "vercel"):
                with mock.patch.dict(os.environ):
                    os.environ.pop("YT_GATEWAY", None)
                    if gateway:
                        os.environ["YT_GATEWAY"] = gateway
                    code, _, err = self.run_stage("--rich", "--deliberate", "critique",
                                                  "--reviewer-model", value)
                self.assertEqual(code, 2, (value, gateway))
                self.assertIn("claude or codex model", err)
                self.assertEqual(Fake.built, 0)

    def test_without_a_review_the_authors_family_is_not_checked(self):
        # `yt run --quick` hands styles --model opus and no reviewer: no
        # critique runs, so nothing judges and nothing is refused
        code, _, err = self.run_stage("--rich", "--model", "opus")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.asked(styles.IDENTITY_SCHEMA), [])

    def test_a_weak_identity_warns_and_exits_zero(self):
        Fake.judgements = [SWAPPABLE, SWAPPABLE]
        code, out, err = self.run_stage("--rich", "--deliberate", "critique")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: identity: weak", err)
        saved = json.loads((self.film / "styles.json").read_text())
        self.assertEqual(saved["identity"]["review"]["verdict"], "weak")
        md = (self.film / "styles.md").read_text()
        self.assertIn("## Identity", md)
        self.assertIn("weak", md)

    def test_an_unevidenced_review_warns_and_exits_zero(self):
        Fake.judgements = [harness.ClaudeError("down"), harness.ClaudeError("down")]
        code, _, err = self.run_stage("--rich", "--deliberate", "critique")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: identity: not_evidenced", err)
        saved = json.loads((self.film / "styles.json").read_text())
        self.assertEqual(saved["identity"]["review"]["verdict"], "not_evidenced")

    def test_a_pass_is_written_and_printed(self):
        code, out, err = self.run_stage("--rich", "--deliberate", "critique")
        self.assertEqual(code, 0, err)
        self.assertNotIn("warning: identity", err)
        self.assertIn(IDENTITY["atmosphere"], out)
        self.assertIn(IDENTITY["signature"], out)
        saved = json.loads((self.film / "styles.json").read_text())
        self.assertEqual({k: saved["identity"][k] for k in styles.IDENTITY_FIELDS}, IDENTITY)
        self.assertEqual(saved["identity"]["review"]["verdict"], "pass")
        md = (self.film / "styles.md").read_text()
        self.assertIn("## Identity", md)
        for value in IDENTITY.values():
            self.assertIn(value, md)
        self.assertIn("pass", md.split("## Identity", 1)[1])

    def test_fresh_asks_again(self):
        self.run_stage("--rich", "--deliberate", "critique")
        first = len(Fake.calls)
        self.run_stage("--rich", "--deliberate", "critique")
        self.assertEqual(len(Fake.calls), first)          # served from decisions/
        self.run_stage("--rich", "--deliberate", "critique", "--fresh")
        self.assertEqual(len(Fake.calls), 2 * first)


class Output(unittest.TestCase):
    idea = Idea(title="Why the bridge fell", genre="explainer", description="d")

    def write(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            js, md = Path(tmp) / "styles.json", Path(tmp) / "styles.md"
            picks = [styles.Style.from_raw(r) for r in data["styles"]]
            styles.write_json(str(js), self.idea, data, picks, usage={"cost_usd": 0})
            styles.write_md(str(md), self.idea, data, picks)
            return json.loads(js.read_text()), md.read_text()

    def test_without_an_identity_nothing_is_added(self):
        bare = copy.deepcopy(CHOICE)
        bare.pop("identity")
        doc, md = self.write(bare)
        self.assertNotIn("identity", doc)
        self.assertNotIn("Identity", md)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            styles.print_styles(self.idea, bare, [styles.Style.from_raw(bare["styles"][0])])
        self.assertNotIn("identity", out.getvalue().lower())

    def test_the_identity_and_its_review_are_written(self):
        data = copy.deepcopy(CHOICE)
        data["identity"]["review"] = {"verdict": "weak", "objection": "o",
                                      "scores": {"fit_to_subject": 5.0, "distinct": 4.0}}
        doc, md = self.write(data)
        self.assertEqual(doc["identity"], data["identity"])
        section = md.split("## Identity", 1)[1]
        self.assertIn("weak", section)
        self.assertIn("o", section)


class Carried(unittest.TestCase):
    """The identity reaches the keyframe and vfx authors; without one their
    briefs are byte-identical to before, so no cached decision moves."""

    DIRECTION = {"message": "m", "combination": "c",
                 "styles": [{"name": "Diagram", "fit": 9, "shows_well": "s", "palette": "p",
                             "typography": "t", "motion": "mo", "sound": "so",
                             "shots": ["a"]}]}

    def test_without_an_identity_the_briefs_are_unchanged(self):
        self.assertEqual(sha(keyframe.direction_block(self.DIRECTION)), "e64c8048433ed2a0")
        self.assertEqual(sha(vfx.VfxPlanner._direction_block(
            SimpleNamespace(direction=self.DIRECTION))), "9ed41049b927f49c")

    def test_with_one_both_briefs_carry_the_identity_line(self):
        direction = dict(self.DIRECTION, identity=dict(
            IDENTITY, review={"verdict": "pass"}))
        for text, plain in (
                (keyframe.direction_block(direction), keyframe.direction_block(self.DIRECTION)),
                (vfx.VfxPlanner._direction_block(SimpleNamespace(direction=direction)),
                 vfx.VfxPlanner._direction_block(SimpleNamespace(direction=self.DIRECTION)))):
            self.assertTrue(text.startswith(plain), text)
            line = text[len(plain):].strip()
            self.assertTrue(line.startswith("Identity:"), line)
            for key in ("atmosphere", "personality", "motif", "signature"):
                self.assertIn(f"{key}: {IDENTITY[key]}", line)
            self.assertNotIn("pass", line)
            self.assertEqual(len(line.splitlines()), 1)


class Modules(unittest.TestCase):
    def test_the_reviewer_default_is_the_vfx_reviewers(self):
        self.assertEqual(styles.REVIEWER_MODEL, vfx.REVIEWER_MODEL)

    def test_styles_does_not_import_vfx_for_it(self):
        # styles.py names its own constant: no import of the vfx module. The
        # card's subprocess check (`ytstudio.vfx` not in sys.modules after
        # `import ytstudio.styles`) cannot hold: styles already reaches vfx
        # through assess, so this pins the narrower rule.
        tree = ast.parse((ROOT / "ytstudio" / "styles.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "vfx", ast.dump(node))
                self.assertFalse((node.module or "").endswith(".vfx"), ast.dump(node))
                self.assertNotIn("vfx", [a.name for a in node.names], ast.dump(node))
            elif isinstance(node, ast.Import):
                self.assertFalse(any("vfx" in a.name for a in node.names), ast.dump(node))


if __name__ == "__main__":
    unittest.main()
