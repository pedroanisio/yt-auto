"""cutcompare.py: the previous kept cut and the new one, watched whole by a
video model through the gateway, in both orders, filed as AUTO-108's tally
with `source: "video"` (AUTO-128).

QA never watches or hears the film: it reads stills and bursts. A model
that takes video can watch both cuts, picture and sound, in one request.
`yt qa --against FILE --compare-model MODEL` asks it twice, once with the
previous cut as Cut A and once with the new one, on its own gateway
harness (no revision notes, a timeout of its own), and files a tally entry
per category only where both orders agree; a disagreement is a tie, and
`cannot_tell` files nothing (Zheng et al. 2023, §3.3-3.4). A comparison
that fails, breaks its schema or is skipped files no entries, and the
still pairs of AUTO-108 run as before. `yt improve --compare-model M`
hands QA the snapshot's video.mp4 wherever it runs QA. Without the option
nothing changes. No model, no network: a fake `node` answers
(tests/gateway_fakes.py), the fake claude engine and renderer of
test_qa_side_by_side read the stills, and the fake runner of test_improve
stands in for the stages.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from gateway_fakes import MODEL, USAGE, FakeNode, err, ok  # noqa: E402
from ytstudio import cutcompare, harness, improve, qa, spend  # noqa: E402
from ytstudio.core import read_json, write_json  # noqa: E402
from tests.test_improve import FakeRunner, ImproveCase, card, mech  # noqa: E402
from tests.test_qa_side_by_side import Pass  # noqa: E402

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"]}
PRICES = json.dumps({MODEL: {"input": 0.75, "output": 3.75}})


def verdict(winner="same", default="same", **cats) -> dict:
    """One order's answer: every category once, `default` unless named."""
    return {"winner": winner, "reasons": ["what was watched"],
            "criteria": [{"category": c, "winner": cats.get(c, default),
                          "reason": f"{c} as watched", "timestamps": [1.5]}
                         for c in qa.CATEGORIES]}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# the engine: a label rides immediately before its file
# --------------------------------------------------------------------------- #

class Labels(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode(self)
        self.a = self.node.dir / "one.mp4"
        self.b = self.node.dir / "two.mp4"
        self.a.write_bytes(b"first")
        self.b.write_bytes(b"second")

    def test_a_labelled_file_carries_its_label_and_a_plain_one_is_as_before(self):
        self.node.engine().ask_json("P", SCHEMA, media=[{"path": self.a, "label": "Cut A:"},
                                                        self.b])
        (req,) = self.node.requests()
        self.assertEqual(req["media"], [
            {"path": str(self.a.resolve()), "mediaType": "video/mp4", "label": "Cut A:"},
            {"path": str(self.b.resolve()), "mediaType": "video/mp4"}])

    def test_the_digest_names_a_label_only_where_one_is_given(self):
        root = self.node.dir / "project"
        h = harness.Harness(root, "qa", model=MODEL, engine="gateway", verbose=False)
        plain = h._media_digest([self.a])
        self.assertEqual(plain, [[sha(self.a), "video/mp4"]])     # as before labels
        self.assertEqual(h._media_digest([{"path": self.a, "label": "Cut A:"}]),
                         [[sha(self.a), "video/mp4", "Cut A:"]])
        self.assertEqual(h._media_digest([{"path": self.a}]), plain)


# --------------------------------------------------------------------------- #
# the comparison itself
# --------------------------------------------------------------------------- #

class Compare(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode(self)
        self.root = self.node.dir / "project"
        (self.root / "improve" / "pass-01" / "snapshot").mkdir(parents=True)
        self.previous = self.root / "improve" / "pass-01" / "snapshot" / "video.mp4"
        self.new = self.root / "video.mp4"
        self.previous.write_bytes(b"the kept cut")
        self.new.write_bytes(b"the revised cut")
        self.h = self.harness()
        self.media: list[list[tuple]] = []
        decide = self.h.decide

        def spy(name, prompt, schema, *, media=()):
            # the cuts are linked into a temporary directory that is gone
            # afterwards, so what each request carried is read as it is asked
            self.media.append([(m["label"], Path(m["path"]).name, sha(m["path"]))
                               for m in media])
            return decide(name, prompt, schema, media=media)
        self.h.decide = spy

    def harness(self) -> harness.Harness:
        return harness.Harness(self.root, "qa", model=MODEL, engine="gateway",
                               role=cutcompare.COMPARE_ROLE, feedback=False, verbose=False,
                               timeout=cutcompare.COMPARE_TIMEOUT)

    def compare(self):
        return cutcompare.compare_cuts(self.h, self.previous, self.new, pass_name="pass-02")

    def test_compare_runs_both_orders(self):
        self.node.replies(ok(verdict()), ok(verdict()))
        got = self.compare()
        self.assertEqual(got["status"], "done", got)
        self.assertEqual(self.node.count, 2)
        old, new = sha(self.previous), sha(self.new)
        self.assertEqual(self.media, [
            [("Cut A:", "cut-a.mp4", old), ("Cut B:", "cut-b.mp4", new)],
            [("Cut A:", "cut-a.mp4", new), ("Cut B:", "cut-b.mp4", old)]])
        ab, ba = self.node.requests()
        for req in (ab, ba):
            self.assertEqual([m["label"] for m in req["media"]], ["Cut A:", "Cut B:"])
            self.assertEqual([Path(m["path"]).name for m in req["media"]],
                             ["cut-a.mp4", "cut-b.mp4"])
            self.assertEqual(req["timeoutMs"], int(cutcompare.COMPARE_TIMEOUT * 1000))
        self.assertEqual(ab["prompt"], ba["prompt"])
        self.assertEqual([got["orders"][o]["decision"] for o in ("ab", "ba")],
                         ["cutcompare-pass-02-ab", "cutcompare-pass-02-ba"])
        self.assertEqual((got["orders"]["ab"]["A"], got["orders"]["ba"]["A"]),
                         ("previous", "new"))
        self.assertEqual(got["cuts"]["previous"], {"path": str(self.previous), "sha256": old,
                                                   "bytes": len(b"the kept cut")})
        self.assertEqual(got["cuts"]["new"]["sha256"], new)
        self.assertEqual(got["orders"]["ab"]["answer"], verdict())
        # nothing is left behind but the decisions
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["decisions", "improve", "video.mp4"])

    def test_compare_hides_which_is_new(self):
        self.node.replies(ok(verdict()))
        self.compare()
        words = ("snapshot", "pass-", "previous", "new", "improve", "kept", "revised",
                 "earlier", "later", "before", "after")
        for req in self.node.requests():
            seen = [req["prompt"].lower(), cutcompare.COMPARE_ROLE.lower(),
                    *[Path(m["path"]).name for m in req["media"]],
                    *[m["label"].lower() for m in req["media"]]]
            for text in seen:
                for word in words:
                    self.assertNotIn(word, text, f"{word!r} in {text[:80]!r}")
            # no score, no number of any kind
            self.assertIsNone(re.search(r"\d", req["prompt"]), req["prompt"])
            self.assertNotIn("Previous pass", req["instructions"])

    def test_a_cached_order_is_not_asked_again(self):
        self.node.replies(ok(verdict()))
        self.compare()
        self.h = self.harness()
        self.assertEqual(cutcompare.compare_cuts(self.h, self.previous, self.new,
                                                 pass_name="pass-02")["status"], "done")
        self.assertEqual(self.node.count, 2)

    def test_enum_violation_after_retry_fails_comparison(self):
        self.node.replies(ok(verdict(winner="C")))
        got = self.compare()
        self.assertEqual(self.node.count, 2)          # the harness's one retry, then no ba
        self.assertEqual(got["status"], "failed")
        self.assertIn("winner", got["reason"])
        self.assertEqual((got["entries"], got["tally"]["total"]),
                         ([], {"better": 0, "worse": 0, "same": 0}))
        # forgotten, so the next run asks again rather than failing the same way
        self.assertFalse(self.h.path("cutcompare-pass-02-ab").exists())
        self.assertEqual(got["orders"]["ab"]["answer"]["winner"], "C")

    def test_a_category_that_is_unknown_repeated_or_missing_fails_the_comparison(self):
        for broken in (
                dict(verdict(), criteria=verdict()["criteria"] + [
                    {"category": "vibes", "winner": "A", "reason": "r", "timestamps": []}]),
                dict(verdict(), criteria=verdict()["criteria"] + verdict()["criteria"][:1]),
                dict(verdict(), criteria=verdict()["criteria"][1:]),
                dict(verdict(), criteria=[dict(c, timestamps=["soon"])
                                          for c in verdict()["criteria"]]),
                dict(verdict(), reasons="one string")):
            with self.subTest(broken=str(broken)[-80:]):
                self.node.replies(ok(broken))
                self.h.fresh = True
                got = self.compare()
                self.assertEqual(got["status"], "failed", got)
                self.assertEqual(got["entries"], [])
                self.assertFalse(self.h.path("cutcompare-pass-02-ab").exists())

    def test_timeout_fails_comparison(self):
        with mock.patch.dict(os.environ, {"GATEWAY_PRICES": PRICES}):
            self.node.replies(err("other", 1, timeout=True, usage=USAGE,
                                  message="The operation was aborted due to timeout"))
            got = self.compare()
        self.assertEqual(got["status"], "failed")
        self.assertIn("timeout", got["reason"])
        self.assertEqual(got["entries"], [])
        report = self.h.report()
        (row,) = report["decisions"]
        self.assertEqual((row["name"], row["kind"]), ("cutcompare-pass-02-ab", "failed"))
        self.assertGreater(row["cost_usd"], 0)
        self.assertAlmostEqual(report["cost_usd"], row["cost_usd"])

    def test_reconcile_to_tally(self):
        # In ab the previous cut is A; in ba the new one is.
        ab = verdict(winner="B", visual_quality="B", animation_vfx="A", cinematography="same",
                     editing_pacing="B", voiceover="cannot_tell", sound_design="A")
        ba = verdict(winner="A", visual_quality="A", animation_vfx="B", cinematography="same",
                     editing_pacing="B", voiceover="A", sound_design="cannot_tell")
        rows, entries, overall = cutcompare.reconcile(ab, ba)
        by = {r["category"]: r for r in rows}
        self.assertEqual(overall, "better")
        self.assertEqual(by["visual_quality"]["result"], "better")
        self.assertEqual(by["animation_vfx"]["result"], "worse")
        self.assertEqual(by["cinematography"]["result"], "same")
        # the orders disagree (both said B, which is a different cut each
        # time): a tie, recorded as such
        self.assertEqual(by["editing_pacing"]["result"], "same")
        self.assertFalse(by["editing_pacing"]["agreed"])
        self.assertEqual((by["editing_pacing"]["ab"], by["editing_pacing"]["ba"]),
                         ({"said": "B", "cut": "new"}, {"said": "B", "cut": "previous"}))
        self.assertIsNone(by["voiceover"]["result"])
        self.assertIsNone(by["sound_design"]["result"])
        self.assertEqual({e["category"] for e in entries},
                         set(qa.CATEGORIES) - {"voiceover", "sound_design"})
        for e in entries:
            self.assertEqual(e["source"], "video")
            self.assertIn(e["result"], qa.SIDE_RESULTS)
            self.assertIn("moment", e)
            self.assertIn("t", e)
        tally = qa.tally(entries)
        self.assertEqual(tally["visual_quality"], {"better": 1, "worse": 0, "same": 0})
        self.assertEqual(tally["animation_vfx"], {"better": 0, "worse": 1, "same": 0})
        self.assertEqual(tally["total"], {"better": 1, "worse": 1,
                                          "same": len(qa.CATEGORIES) - 4})

    def test_a_single_order_never_files_an_entry(self):
        self.node.replies(ok(verdict(default="B")), err("gateway", 3, status=400,
                                                        retryable=False, message="bad"))
        got = self.compare()
        self.assertEqual(got["status"], "failed")
        self.assertEqual(got["entries"], [])
        self.assertEqual(got["criteria"], [])


# --------------------------------------------------------------------------- #
# yt qa --against
# --------------------------------------------------------------------------- #

class QaCase(Pass):
    def setUp(self):
        super().setUp()
        self.node = FakeNode(self)
        self.old = self.tmp / "kept.mp4"
        self.old.write_bytes(b"the kept cut")
        self.node.replies(ok(verdict(default="B")), ok(verdict(default="A")))

    def video(self, data=b"the revised cut"):
        path = self.root / "video.mp4"
        path.write_bytes(data)
        later = (self.root / "scene.xml").stat().st_mtime + 10
        os.utime(path, (later, later))

    def against(self, **kw):
        """pass-01, then a new cut scored with --against the kept one."""
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.video()
        kw.setdefault("against", str(self.old))
        kw.setdefault("compare_model", MODEL)
        return self.run_qa(**kw)

    def comparison(self, name="pass-02"):
        return json.loads((self.root / "qa" / name / "comparison.json").read_text())


class Qa(QaCase):
    def test_compare_uses_own_harness(self):
        (self.root / "feedback").mkdir()
        (self.root / "feedback" / "qa.md").write_text("REVISION NOTE: tighten the hook")
        self.against()
        c = self.card()
        self.assertEqual(self.node.count, 2)
        names = [d["name"] for d in c["engine"]["decisions"]]
        self.assertFalse([n for n in names if "cutcompare" in n], names)
        self.assertEqual([d["name"] for d in c["compare_engine"]["decisions"]],
                         ["cutcompare-pass-02-ab", "cutcompare-pass-02-ba"])
        self.assertEqual(c["compare_engine"]["model"], MODEL)
        self.assertEqual(c["engine"]["model"], "sonnet")
        for req in self.node.requests():
            self.assertNotIn("REVISION NOTE", req["instructions"])
            self.assertNotIn("REVISION NOTE", req["prompt"])
        # the video comparison succeeded: the still pairs were not asked
        self.assertEqual(self.seen["compares"], [])
        side = c["side_by_side"]
        self.assertEqual((side["status"], side["against"], side["source"]),
                         ("done", "pass-01", "video"))
        self.assertTrue(side["entries"])
        self.assertTrue(all(e["source"] == "video" for e in side["entries"]))
        # previous is A in ab and said B; new is A in ba and said A: better everywhere
        self.assertEqual(side["tally"]["total"]["better"], len(qa.CATEGORIES))
        self.assertEqual(c["against"]["status"], "done")
        got = self.comparison()
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["model"], MODEL)
        self.assertEqual(got["cuts"]["new"]["sha256"], sha(self.root / "video.mp4"))
        self.assertEqual(got["cuts"]["previous"]["sha256"], sha(self.old))
        self.assertEqual(set(got["orders"]), {"ab", "ba"})
        self.assertEqual(got["entries"], side["entries"])
        self.assertGreater(got["cost_usd"], 0)
        self.assertIn("Video comparison", (self.root / "qa" / "pass-02" /
                                           "findings.md").read_text())

    def test_compare_cost_counted_once(self):
        self.against()
        c = self.card()
        reviewer, compare = c["engine"]["cost_usd"], c["compare_engine"]["cost_usd"]
        self.assertGreater(compare, 0)
        self.assertAlmostEqual(c["usage"]["cost_usd"], round(reviewer + compare, 4))
        self.assertEqual(c["usage"]["calls"], c["engine"]["calls"] + c["compare_engine"]["calls"])
        self.assertIn(MODEL, c["usage"]["by_model"])
        self.assertEqual(read_json(self.root / "qa.json")["usage"], c["usage"])
        # the compare cost is asked once a pass, never scaled by the samples
        self.assertAlmostEqual(
            qa.pass_usd(c), round((c["usage"]["cost_usd"] - compare - _once(c))
                                  / c["scoring"]["asked"] * qa.QA_SAMPLES
                                  + compare + _once(c), 2))

    def test_enum_violation_falls_back_to_the_still_pairs(self):
        self.node.replies(ok(verdict(winner="C")))
        self.against()
        c = self.card()
        self.assertEqual(self.comparison()["status"], "failed")
        side = c["side_by_side"]
        self.assertEqual(side.get("source", "stills"), "stills")
        self.assertEqual(len(self.seen["compares"]), 1)      # the still pairs ran instead
        self.assertTrue(all(e["source"] == "stills" for e in side["entries"]))
        self.assertTrue(any("video comparison" in n for n in c["not_evidenced"]))

    def test_timeout_fails_comparison_and_qa_exits_normally(self):
        with mock.patch.dict(os.environ, {"GATEWAY_PRICES": PRICES}):
            self.node.replies(err("other", 1, timeout=True, usage=USAGE,
                                  message="The operation was aborted due to timeout"))
            code = self.against()
        self.assertEqual(code, 1)                           # NOT READY, as without it
        c = self.card()
        self.assertEqual(c["against"]["status"], "failed")
        (row,) = c["compare_engine"]["decisions"]
        self.assertEqual(row["kind"], "failed")
        self.assertGreater(row["cost_usd"], 0)
        self.assertAlmostEqual(c["usage"]["cost_usd"],
                               round(c["engine"]["cost_usd"] + row["cost_usd"], 4))
        self.assertEqual(c["side_by_side"].get("source", "stills"), "stills")

    def test_missing_previous_cut(self):
        self.against(against=str(self.tmp / "gone.mp4"))
        got = self.comparison()
        self.assertEqual(got["status"], "skipped")
        self.assertIn("gone.mp4", got["reason"])
        self.assertEqual(self.node.count, 0)
        self.assertEqual(self.card()["side_by_side"].get("source", "stills"), "stills")

    def test_a_cut_older_than_its_document_or_missing_is_not_compared(self):
        self.run_qa()
        (self.root / "scene.xml").write_text("<scene cut='2'/>")
        self.run_qa(against=str(self.old), compare_model=MODEL)
        self.assertIn("video.mp4", self.comparison()["reason"])
        (self.root / "video.mp4").write_bytes(b"stale")
        os.utime(self.root / "video.mp4", (1, 1))
        self.run_qa(against=str(self.old), compare_model=MODEL)
        self.assertEqual(self.comparison("pass-03")["status"], "skipped")
        self.assertIn("older", self.comparison("pass-03")["reason"])
        self.assertEqual(self.node.count, 0)

    def test_oversize_skips(self):
        with mock.patch.object(cutcompare, "COMPARE_MAX_MB", 0.00001):
            self.against()
        got = self.comparison()
        self.assertEqual(got["status"], "skipped")
        self.assertIn(str(len(b"the kept cut")), got["reason"])
        self.assertIn(str(len(b"the revised cut")), got["reason"])
        self.assertEqual(self.node.count, 0)
        self.assertEqual(len(self.seen["compares"]), 1)

    def test_without_the_gateway_key_the_comparison_is_skipped_not_failed(self):
        with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": ""}):
            self.against()
        got = self.comparison()
        self.assertEqual(got["status"], "skipped")
        self.assertIn("AI_GATEWAY_API_KEY", got["reason"])
        self.assertEqual(self.node.count, 0)

    def test_compare_model_from_the_environment(self):
        with mock.patch.dict(os.environ, {cutcompare.MODEL_ENV: MODEL}):
            self.against(compare_model=None)
        self.assertEqual(self.comparison()["status"], "done")


def _once(c) -> float:
    return sum(d["cost_usd"] for d in c["engine"]["decisions"]
               if d["name"].endswith("-compare-pass") and not d["cached"])


class Refused(QaCase):
    def refused(self, **kw):
        err_ = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err_):
            code = qa.run(self.args(**kw))
        self.assertEqual(code, 2, err_.getvalue())
        self.assertEqual(self.seen["renders"], [])           # before any frame
        self.assertFalse((self.root / "qa").exists())
        self.assertEqual(self.node.count, 0)
        return err_.getvalue()

    def test_compare_model_same_family_refused(self):
        write_json(self.root / "vfx.json", {"usage": spend.record(1.0, "sonnet", 3)})
        said = self.refused(against=str(self.old),
                            compare_model="anthropic/claude-sonnet-5")
        self.assertIn("vfx.json", said)
        self.assertIn("family", said)

    def test_either_option_without_the_other_is_refused(self):
        self.assertIn("--compare-model", self.refused(against=str(self.old)))
        self.assertIn("--against", self.refused(compare_model=MODEL))

    def test_a_compare_model_the_gateway_engine_cannot_ask_is_refused(self):
        for model in ("gemini", "vmc/judge", "claude-code/anthropic/claude-opus-5"):
            with self.subTest(model=model):
                self.refused(against=str(self.old), compare_model=model)

    def test_the_environment_alone_turns_nothing_on(self):
        with mock.patch.dict(os.environ, {cutcompare.MODEL_ENV: MODEL}):
            self.against(against=None, compare_model=None)
        self.assertEqual(self.node.count, 0)
        self.assertFalse((self.root / "qa" / "pass-02" / "comparison.json").exists())


class Off(QaCase):
    def test_against_off_is_identical(self):
        self.against(against=None, compare_model=None)
        c = self.card()
        self.assertEqual(self.node.count, 0)
        self.assertNotIn("against", c)
        self.assertNotIn("compare_engine", c)
        self.assertEqual(c["usage"], spend.from_report(c["engine"]))
        self.assertEqual(c["side_by_side"]["entries"][0]["source"], "stills")
        self.assertNotIn("source", c["side_by_side"])
        self.assertFalse((self.root / "qa" / "pass-02" / "comparison.json").exists())
        self.assertFalse([p for p in (self.root / "decisions" / "qa").iterdir()
                          if "cutcompare" in p.name])


# --------------------------------------------------------------------------- #
# yt improve --compare-model
# --------------------------------------------------------------------------- #

class Improve(ImproveCase):
    def setUp(self):
        super().setUp()
        saved = os.environ.pop(cutcompare.MODEL_ENV, None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, cutcompare.MODEL_ENV, saved)

    def qa_runs(self, runner):
        return [c for c in runner.calls if c[0] == "qa"]

    def against_of(self, argv):
        return argv[argv.index("--against") + 1], argv[argv.index("--compare-model") + 1]

    def test_the_main_pass_hands_qa_the_snapshot_cut(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1, compare_model=MODEL).run()
        (argv,) = self.qa_runs(runner)
        snap = self.root / "improve" / "pass-01" / "snapshot"
        self.assertEqual(self.against_of(argv), (str(snap / "video.mp4"), MODEL))
        self.assertEqual((snap / "video.mp4").read_bytes(), b"original video")
        self.assertEqual(argv[argv.index("--previous") + 1], "pass-01")
        recorded = [a for p in record["passes"] for a in p["argv"] if a[0] == "qa"]
        self.assertEqual(recorded, [argv])
        self.assertNotIn("compare", record["passes"][0])

    def test_missing_previous_cut(self):
        (self.root / "video.mp4").unlink()
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, max_passes=1, compare_model=MODEL).run()
        (argv,) = self.qa_runs(runner)
        self.assertNotIn("--against", argv)
        self.assertNotIn("--compare-model", argv)
        self.assertEqual(record["passes"][0]["compare"],
                         "no previous cut: the snapshot holds no video.mp4")

    def test_all_qa_call_sites_pass_against(self):
        # the re-judge path: a reverted pass, judged again
        write_json(self.root / "gate.json", {"exit": 0})
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [card(4.9)], fail={"gate": "refuse"}),
                      max_passes=1, compare_model=MODEL).run()
        runner = FakeRunner(self.root, [card(6.0)], fail={"gate": "refuse"})
        record = self.improver(runner, rejudge=True, max_passes=1, compare_model=MODEL).run()
        (argv,) = self.qa_runs(runner)
        fresh = self.root / "improve" / "pass-01" / "snapshot-rejudge"
        self.assertEqual(self.against_of(argv), (str(fresh / "video.mp4"), MODEL))
        self.assertEqual([a for a in record["passes"][-1]["argv"] if a[0] == "qa"], [argv])

    def test_an_unjudged_pass_is_scored_against_its_own_snapshot(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [], fail={"qa": "no-pass"}),
                      compare_model=MODEL).run()
        runner = FakeRunner(self.root, [card(6.0)])
        self.improver(runner, max_passes=1, compare_model=MODEL).run()
        argv = self.qa_runs(runner)[0]
        snap = self.root / "improve" / "pass-01" / "snapshot"
        self.assertEqual(self.against_of(argv), (str(snap / "video.mp4"), MODEL))

    def test_a_resumed_pass_records_and_runs_the_same_argv(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        self.improver(FakeRunner(self.root, [], fail={"render": 1}),
                      compare_model=MODEL).run()
        runner = FakeRunner(self.root, [card(6.0)])
        record = self.improver(runner, resume=True, max_passes=1, compare_model=MODEL).run()
        (argv,) = self.qa_runs(runner)
        snap = self.root / "improve" / "pass-01" / "snapshot"
        self.assertEqual(self.against_of(argv), (str(snap / "video.mp4"), MODEL))
        self.assertEqual([a for a in record["passes"][-1]["argv"] if a[0] == "qa"], [argv])

    def test_against_off_is_identical(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        runner = FakeRunner(self.root, [card(6.0)])
        improver = self.improver(runner, max_passes=1)
        record = improver.run()
        (argv,) = self.qa_runs(runner)
        self.assertEqual(argv, ["qa", *improver.base, "--previous", "pass-01"])
        entry = record["passes"][0]
        self.assertNotIn("compare", entry)
        self.assertEqual([a for a in entry["argv"] if a[0] == "qa"], [argv])
        qa_row = next(r for r in entry["estimates"] if r["stage"] == "qa")
        self.assertNotIn("--against", qa_row["source"])
        # the rejudge and resume paths are as they were too
        self.assertEqual(improver._qa_argv(None, self.root / "improve" / "pass-01" /
                                           "snapshot"), ["qa", *improver.base])

    def test_compare_cost_counted_once(self):
        self.start_pass(card(5.0, mechanical=[mech()]))
        scored = dict(card(6.0), usage=spend.record(0.4321, "sonnet", 5))
        improver = self.improver(FakeRunner(self.root, [scored]), max_passes=1,
                                 compare_model=MODEL)
        record = improver.run()
        self.assertAlmostEqual(record["passes"][0]["cost_usd"], 0.4321)
        self.assertAlmostEqual(improver.spent_stages, 0.4321)

    def test_compare_model_same_family_refused(self):
        args = argparse.Namespace(model="sonnet", compare_model="anthropic/claude-sonnet-5",
                                  quiet=True)
        with mock.patch.object(improve, "Improver", side_effect=AssertionError("ran")), \
                mock.patch.object(improve.workspace, "resolve",
                                  side_effect=AssertionError("resolved")), \
                contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertEqual(improve.run(args), 2)
        self.assertIn("--compare-model", said.getvalue())
        for model in ("gemini", "vmc/judge"):
            args.compare_model = model
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(improve.run(args), 2)
        args.compare_model = None
        with mock.patch.dict(os.environ, {cutcompare.MODEL_ENV: "anthropic/claude-sonnet-5"}), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(improve.run(args), 2)

    def test_a_compare_model_of_a_recorded_author_is_refused_before_any_stage(self):
        # flags.model_family: a non-Claude id is its own family until
        # AUTO-125's maker() names families across a provider's models
        write_json(self.root / "vfx.json", {"usage": spend.record(1.0, "gemini-3.6-flash", 3)})
        args = argparse.Namespace(model="sonnet", compare_model="google/gemini-3.6-flash",
                                  quiet=True, project=str(self.root))
        with mock.patch.object(improve, "Improver", side_effect=AssertionError("ran")), \
                contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertEqual(improve.run(args), 2)
        self.assertIn("vfx.json", said.getvalue())


class Estimate(ImproveCase):
    def qa_row(self, argv):
        rows = improve.stage_estimates(self.root, ["sound"], extras={"qa": list(argv)})
        return next(r for r in rows if r["stage"] == "qa")

    def test_an_unmeasured_comparison_adds_compare_usd_once(self):
        self.start_pass(dict(card(7.0), usage=spend.record(1.5, "m", 3),
                             scoring={"asked": 3, "valid": 3}))
        plain = self.qa_row(["qa", "--previous", "pass-01"])
        row = self.qa_row(["qa", "--previous", "pass-01", "--against", "x.mp4",
                           "--compare-model", MODEL])
        self.assertAlmostEqual(row["usd"], round(plain["usd"] + spend.COMPARE_USD, 2))
        self.assertIn("--against", row["source"])

    def test_a_measured_comparison_is_not_added_again(self):
        compare = {"cost_usd": 0.06, "decisions": [
            {"name": "cutcompare-pass-01-ab", "cost_usd": 0.03, "cached": False},
            {"name": "cutcompare-pass-01-ba", "cost_usd": 0.03, "cached": False}]}
        self.start_pass(dict(card(7.0), usage=spend.record(1.56, "m", 5),
                             scoring={"asked": 3, "valid": 3},
                             engine={"cost_usd": 1.5, "decisions": [
                                 {"name": f"pass-01-scorecard-s{k}", "cost_usd": 0.5}
                                 for k in (1, 2, 3)]},
                             compare_engine=compare))
        row = self.qa_row(["qa", "--against", "x.mp4", "--compare-model", MODEL])
        self.assertEqual(row["basis"], "measured")
        self.assertNotIn("--against", row["source"])
        self.assertAlmostEqual(qa.pass_usd(read_json(self.root / "qa" / "pass-01" /
                                                     "scorecard.json")), 1.56)


if __name__ == "__main__":
    unittest.main()
