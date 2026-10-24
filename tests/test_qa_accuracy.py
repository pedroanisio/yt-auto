"""qa.py's on-screen accuracy check (AUTO-150).

With a dossier, `yt qa` reads the text vfx authored into the film's
documents (text, formula and chart assets, through each include, instance
and nested include) and has a reviewer of another family than every author
check it against the dossier. These pin the reader of the documents, the
answer's validation, the retry and the refusal. Every model call is faked.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytstudio import cutcompare, harness, qa, script, vfx
from ytstudio.harness import HarnessError

#: One vfx document: a symbol drawing a text, a formula (inside a group)
#: and a chart; a text asset nobody draws; an image the symbol draws.
WORLD = """<scene version="1.3">
  <assets>
    <text id="t-pct" text="40%"/>
    <formula id="f-law" tex="E=mc^2"/>
    <chart id="c-bars" labels="a,b"><series name="s" values="1 2"/></chart>
    <text id="t-unused" text="never drawn"/>
    <image id="i-photo" src="photo.png"/>
  </assets>
  <symbols>
    <symbol id="world-a" width="1920" height="1080">
      <layer id="l-pct" asset="t-pct"/>
      <group id="g"><layer id="l-law" asset="f-law"/></group>
      <layer id="l-bars" asset="c-bars"/>
    </symbol>
    <symbol id="world-b" width="1920" height="1080">
      <layer id="l-photo" asset="i-photo"/>
      <instance id="inner" symbol="label-sym"/>
    </symbol>
    <symbol id="label-sym" width="400" height="100">
      <layer id="l-inner" asset="t-unused"/>
    </symbol>
  </symbols>
</scene>
"""

SCENE = """<scene version="1.3"><project width="1920" height="1080" fps="30" duration="12"/>
  <composition>
    <include id="shot-1.1" start="3" end="9" src="vfx/v01/a.scene.xml" symbol="{symbol}"/>
  </composition>
</scene>
"""

BUILD = {"shots": [{"label": "1.1", "at": 3.0, "seconds": 6.0, "include": "shot-1.1"},
                   {"label": "1.2", "at": 9.0, "seconds": 3.0, "include": "shot-1.2"}]}

DOSSIER = {"premise": "a share rose", "claims": [
    {"claim": "the share rose 14%", "confidence": "high", "source": "S1"}]}


def finding(**over) -> dict:
    row = {"shot": "1.1", "quote": "40%", "claim_id": "c1", "problem": "number-mismatch",
           "severity": "blocking", "fix": "draw 14%", "owner": "vfx"}
    row.update(over)
    return row


class FakeHarness:
    """Stands in for the accuracy harness: answers in turn, records names."""

    model = "opus"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def decide(self, name, prompt, schema, *, media=()):
        self.calls.append({"name": name, "prompt": prompt, "schema": schema,
                           "media": tuple(media)})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def report(self) -> dict:
        return {"model": self.model, "cost_usd": 0.25, "decisions": []}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qa-accuracy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def scene(self, world: str = WORLD, symbol: str = "world-a") -> Path:
        doc = self.tmp / "vfx" / "v01" / "a.scene.xml"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(world)
        path = self.tmp / "scene.xml"
        path.write_text(SCENE.format(symbol=symbol))
        return path


class SceneText(Case):
    def test_scene_text_reads_text_formula_chart(self):
        rows, not_checked = qa.scene_text(self.scene(), BUILD)
        self.assertEqual([(r["kind"], r["text"]) for r in rows], [
            ("text", "40%"), ("formula", "E=mc^2"), ("chart", "a,b | s | 1 2")])
        for r in rows:
            self.assertEqual(r["include"], "shot-1.1")
            self.assertEqual(r["shots"], ["1.1"])
            self.assertEqual((r["start"], r["end"]), (3.0, 9.0))
            self.assertTrue(r["source_doc"].endswith("a.scene.xml"), r)
        self.assertEqual([r["element_id"] for r in rows], ["t-pct", "f-law", "c-bars"])
        # the asset nobody draws never reaches the screen, and is not read
        self.assertNotIn("never drawn", json.dumps(rows))
        self.assertEqual(not_checked, [])
        self.assertEqual(qa.shot_screen(rows), {"1.1": "40% E=mc^2 a,b | s | 1 2"})

    def test_scene_text_follows_instances(self):
        rows, _ = qa.scene_text(self.scene(symbol="world-b"), BUILD)
        self.assertEqual([r["text"] for r in rows], ["never drawn"])
        self.assertEqual(rows[0]["shots"], ["1.1"])

    def test_scene_text_follows_a_nested_include(self):
        inner = self.tmp / "vfx" / "v01" / "inner.scene.xml"
        inner.parent.mkdir(parents=True, exist_ok=True)
        inner.write_text('<scene><assets><text id="t-deep" text="Deep label"/></assets>'
                         '<symbols><symbol id="deep"><layer asset="t-deep"/></symbol>'
                         '</symbols></scene>')
        world = WORLD.replace('<layer id="l-pct" asset="t-pct"/>',
                              '<include id="nest" src="inner.scene.xml" symbol="deep"/>')
        rows, _ = qa.scene_text(self.scene(world), BUILD)
        self.assertEqual(rows[0]["text"], "Deep label")
        self.assertEqual(rows[0]["include"], "shot-1.1")
        self.assertTrue(rows[0]["source_doc"].endswith("inner.scene.xml"))

    def test_scene_text_not_checked(self):
        rows, not_checked = qa.scene_text(self.scene(symbol="world-b"), BUILD)
        self.assertEqual(not_checked, [{"include": "shot-1.1", "element_id": "i-photo",
                                        "kind": "image"}])
        # a missing include file is one entry, never an exception
        (self.tmp / "vfx" / "v01" / "a.scene.xml").unlink()
        rows, not_checked = qa.scene_text(self.tmp / "scene.xml", BUILD)
        self.assertEqual(rows, [])
        self.assertEqual(len(not_checked), 1)
        self.assertEqual(not_checked[0]["include"], "shot-1.1")
        # and an unparsable scene, or none, is no exception either
        broken = self.tmp / "broken.xml"
        broken.write_text("<scene><composition>")
        self.assertEqual(qa.scene_text(broken, BUILD)[0], [])
        self.assertEqual(qa.scene_text(self.tmp / "absent.xml", BUILD)[0], [])


class Validation(Case):
    def test_quote_split_across_nodes(self):
        world = WORLD.replace('<text id="t-pct" text="40%"/>',
                              '<text id="t-rose" text="rose"/><text id="t-pct" text="40%"/>'
                              ).replace('<layer id="l-pct" asset="t-pct"/>',
                                        '<layer asset="t-rose"/><layer asset="t-pct"/>')
        rows, _ = qa.scene_text(self.scene(world), BUILD)
        screen = qa.shot_screen(rows)
        claims = script.claim_table(DOSSIER)
        ok = {"findings": [finding(quote="rose 40%")], "checked": 1, "verdict": "wrong"}
        self.assertEqual(qa.accuracy_problems(ok, screen, claims), [])
        bad = {"findings": [finding(quote="rose 41%")], "checked": 1, "verdict": "wrong"}
        self.assertEqual(len(qa.accuracy_problems(bad, screen, claims)), 1)

    def test_every_field_is_checked(self):
        screen, claims = {"1.1": "40%"}, script.claim_table(DOSSIER)
        for bad in (finding(shot="9.9"), finding(claim_id="c7"), finding(problem="wrong"),
                    finding(owner="sound"), finding(severity="major")):
            self.assertTrue(qa.accuracy_problems({"findings": [bad]}, screen, claims), bad)
        self.assertEqual(qa.accuracy_problems({"findings": [finding(claim_id="none")]},
                                              screen, claims), [])
        # without the claim table (a dossier before AUTO-132's ids) only "none"
        self.assertTrue(qa.accuracy_problems({"findings": [finding()]}, screen, {}))


class Decision(Case):
    def rows(self):
        return qa.scene_text(self.scene(), BUILD)[0]

    def screenplay(self):
        return {"sections": [{"shots": [
            {"label": "1.1", "narration": "The share rose fourteen percent.",
             "on_screen_text": ["14%"]}]}]}

    def test_a_valid_answer_is_recorded_with_its_time(self):
        h = FakeHarness({"findings": [finding()], "checked": 1, "verdict": "one wrong"})
        record = qa.accuracy(h, self.tmp / "pass-02", rows=self.rows(),
                             script=self.screenplay(), dossier=DOSSIER, voice={})
        self.assertEqual([c["name"] for c in h.calls], ["pass-02-accuracy"])
        self.assertIs(h.calls[0]["schema"], qa.ACCURACY_SCHEMA)
        self.assertEqual(h.calls[0]["media"], ())          # no stills, no media
        prompt = h.calls[0]["prompt"]
        block = script.research_block(DOSSIER)
        self.assertEqual(prompt.count(block), 1)
        self.assertIn("40%", prompt)
        self.assertIn("The share rose fourteen percent.", prompt)
        self.assertIn("already fact-checked", prompt)
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["checked"], 1)
        self.assertEqual(record["model"], "opus")
        self.assertEqual(record["findings"][0]["start"], 3.0)
        self.assertIs(record["findings"][0]["in_screenplay"], False)

    def test_invalid_answer_retries_then_fails(self):
        bad = {"findings": [finding(quote="41%")], "checked": 1, "verdict": "x"}
        h = FakeHarness(bad, bad)
        record = qa.accuracy(h, self.tmp / "pass-01", rows=self.rows(),
                             script=self.screenplay(), dossier=DOSSIER, voice={})
        self.assertEqual([c["name"] for c in h.calls],
                         ["pass-01-accuracy", "pass-01-accuracy-retry"])
        self.assertIn("41%", h.calls[1]["prompt"].split("\n\n")[-1])
        self.assertEqual(record["status"], qa.NOT_EVIDENCED)
        self.assertIs(record["bar"], True)
        self.assertEqual(record["command"], "yt qa --fresh")
        self.assertTrue(record["why"].startswith("the accuracy check failed"))
        # a retry that answers well stands
        h = FakeHarness(bad, {"findings": [], "checked": 1, "verdict": "fine"})
        record = qa.accuracy(h, self.tmp / "pass-01", rows=self.rows(),
                             script=self.screenplay(), dossier=DOSSIER, voice={})
        self.assertEqual(record["status"], "done")
        # and an engine that fails is the same failed record
        h = FakeHarness(HarnessError("[qa] pass-01-accuracy: timed out"))
        record = qa.accuracy(h, self.tmp / "pass-01", rows=self.rows(),
                             script=self.screenplay(), dossier=DOSSIER, voice={})
        self.assertEqual((record["status"], record["bar"]), (qa.NOT_EVIDENCED, True))

    def test_nothing_on_screen_asks_nothing(self):
        h = FakeHarness({"findings": [], "checked": 0, "verdict": ""})
        record = qa.accuracy(h, self.tmp / "pass-01", rows=[], script={}, dossier=DOSSIER,
                             voice={}, not_checked=[{"include": "x", "element_id": "i",
                                                     "kind": "image"}])
        self.assertEqual(h.calls, [])
        self.assertEqual(record, {"status": "done", "checked": 0, "findings": [],
                                  "not_checked": [{"include": "x", "element_id": "i",
                                                   "kind": "image"}]})


class Refusal(Case):
    def authors(self, script_model, vfx_model):
        (self.tmp / "script.json").write_text(json.dumps(
            {"usage": {"model": script_model}} if script_model else {"sections": []}))
        (self.tmp / "vfx.json").write_text(json.dumps(
            {"usage": {"model": vfx_model}} if vfx_model else {"authored": []}))
        return cutcompare.author_models({"script": self.tmp / "script.json",
                                         "vfx": self.tmp / "vfx.json"})

    def test_refusal_covers_vfx_author(self):
        authors = self.authors("gpt-5", "opus")
        self.assertTrue(qa.accuracy_refusal("opus", authors))
        self.assertTrue(qa.accuracy_refusal("gpt-5", authors))
        # opus and sonnet are different families under own_reviewer
        self.assertEqual(qa.accuracy_refusal("opus", self.authors("gpt-5", "sonnet")), "")
        self.assertEqual(qa.accuracy_refusal("opus", []), "")

    def test_refusal_unnamed_report(self):
        authors = self.authors("sonnet", None)
        for model in ("opus", "gpt-5"):
            said = qa.accuracy_refusal(model, authors)
            self.assertIn("vfx.json names no model", said)
            self.assertIn(f"--accuracy-model {model}", said)

    def test_improve_adds_its_model(self):
        authors = self.authors("gpt-5", "gpt-5") + [("improve", "opus")]
        self.assertTrue(qa.accuracy_refusal("opus", authors))


class Engine(unittest.TestCase):
    def test_the_default_is_the_vfx_reviewer(self):
        self.assertEqual(qa.ACCURACY_MODEL, vfx.REVIEWER_MODEL)

    def test_the_decision_is_a_plain_cli_harness_with_the_gateway_unset(self):
        env = {k: v for k, v in os.environ.items() if k not in ("YT_GATEWAY", "YT_ENGINE")}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env, clear=True):
            h = qa.accuracy_harness(Path(tmp), None, house="", house_digest={},
                                    fresh=False, verbose=False)
            self.assertIsInstance(h, harness.Harness)
            self.assertIsNone(h.engine)
            self.assertIsNone(h.gateway)
            self.assertEqual((h.stage, h.model, h.role), ("qa", "opus", qa.ACCURACY_ROLE))
            self.assertEqual(qa.accuracy_harness(Path(tmp), "gpt-5", house="",
                                                 house_digest={}, fresh=False,
                                                 verbose=False).model, "gpt-5")

    def test_the_schema_holds_the_enums(self):
        item = qa.ACCURACY_SCHEMA["properties"]["findings"]["items"]
        self.assertEqual(item["properties"]["problem"]["enum"], list(qa.ACCURACY_PROBLEMS))
        self.assertEqual(item["properties"]["owner"]["enum"], list(qa.ACCURACY_OWNERS))
        self.assertEqual(item["properties"]["severity"]["enum"], ["blocking", "minor"])
        self.assertEqual(qa.ACCURACY_PROBLEMS, ("contradicts", "overstates", "unsupported",
                                                "number-mismatch", "unframed-speculation"))
        self.assertEqual(qa.ACCURACY_OWNERS, ("script", "vfx", "upstream-fact"))
        self.assertIn("dossier", qa.ACCURACY_ROLE)


if __name__ == "__main__":
    unittest.main()
