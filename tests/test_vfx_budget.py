"""AUTO-26 / AUTO-27: one budget per vfx section and per stage, and an author
nearing its budget ends on a reviewed build.

A section's budget covers its author sessions and its reviews; the stage's
covers every section. The estimate is printed before the first agent starts,
a section that would overrun stops at its best reviewed round, and an author
session that spends most of its cap is asked for its build and still (the 80%
clock's path) instead of being killed with exit 1. The model is never called:
engines are fakes, and the render host is a stub.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import claude_engine
from claude_engine import Event, Usage
from ytstudio import harness, sdkkit, vfx
from ytstudio.core import Idea
from ytstudio.research import Dossier
from tests import test_vfx_world as world

#: $0.01 a token, so 100 tokens are $1 in the fake streams.
PRICES = {"fake": {"input": 0, "output": 10_000}, "sonnet": {"input": 0, "output": 10_000}}


def message(mid: str, tokens: int, model: str = "fake") -> Event:
    return Event(type="assistant", text="working", raw={"type": "assistant", "message": {
        "id": mid, "model": model, "usage": {"output_tokens": tokens}}})


# --------------------------------------------------------------------------- #
# the account
# --------------------------------------------------------------------------- #

class Account(unittest.TestCase):
    def usage(self, cost: float, timed_out: float = 0.0) -> Usage:
        u = Usage()
        u.cost_usd = cost
        u.timed_out_cost_usd = timed_out
        return u

    def test_a_sections_spend_is_its_author_sessions_plus_its_reviews(self):
        b = vfx.SpendBudget(section_usd=10, stage_usd=30)
        author, review = self.usage(3.0), self.usage(1.5)
        b.track(1, author=author, review=review)
        self.assertAlmostEqual(b.spent(1), 4.5)
        author.cost_usd += 2.0                      # live: a later session shows at once
        self.assertAlmostEqual(b.spent(1), 6.5)
        self.assertAlmostEqual(b.section_left(1), 3.5)

    def test_the_stage_sums_its_sections(self):
        b = vfx.SpendBudget(section_usd=10, stage_usd=12)
        b.track(1, author=self.usage(4), review=self.usage(1))
        b.track(2, author=self.usage(3), review=self.usage(1))
        self.assertAlmostEqual(b.stage_spent(), 9)
        self.assertAlmostEqual(b.stage_left(), 3)

    def test_a_timed_out_calls_spend_is_counted_once(self):
        b = vfx.SpendBudget(section_usd=10, stage_usd=10)
        u = Usage()
        u.add_partial({"output_tokens": 5}, cost=0.5, estimated=True)   # inside cost_usd already
        b.track(1, author=u, review=Usage())
        self.assertAlmostEqual(b.spent(1), 0.5)
        self.assertAlmostEqual(b.report()["sections"][1]["timed_out_usd"], 0.5)

    def test_a_grant_is_the_smaller_of_what_is_asked_and_what_the_budgets_leave(self):
        b = vfx.SpendBudget(section_usd=10, stage_usd=12)
        b.track(1, author=self.usage(4), review=self.usage(0))
        b.track(2, author=self.usage(0), review=self.usage(0))
        self.assertAlmostEqual(b.grant(1, 4.0), 4.0)        # section 1: 6 left, stage 8 left
        # section 1's grant is in flight: the stage has 12 - 4 - 4 = 4 for section 2
        self.assertAlmostEqual(b.grant(2, 6.0), 4.0)
        # nothing is left of the stage for a third asker while both are in flight
        b.track(3, author=self.usage(0), review=self.usage(0))
        self.assertAlmostEqual(b.grant(3, 2.0), 0.0)
        b.release(1)                                         # section 1 spent nothing of it
        self.assertAlmostEqual(b.grant(3, 2.0), 2.0)

    def test_a_grant_stops_at_the_sections_budget(self):
        b = vfx.SpendBudget(section_usd=5, stage_usd=100)
        b.track(1, author=self.usage(4), review=self.usage(0))
        self.assertAlmostEqual(b.grant(1, 4.0), 1.0)
        self.assertEqual(vfx.SpendBudget(section_usd=5, stage_usd=100).grant(9, 4.0), 4.0)

    def test_the_report_gives_each_section_and_the_stage_against_their_budgets(self):
        b = vfx.SpendBudget(section_usd=10, stage_usd=15)
        b.track(1, author=self.usage(4), review=self.usage(1))
        report = b.report()
        self.assertEqual(report["stage"], {"budget_usd": 15, "spent_usd": 5.0})
        row = report["sections"][1]
        self.assertEqual((row["budget_usd"], row["spent_usd"], row["author_usd"],
                          row["review_usd"]), (10, 5.0, 4.0, 1.0))


class Estimate(unittest.TestCase):
    def test_the_estimate_counts_every_round_of_every_section(self):
        e = vfx.estimate_spend(sections=4, revisions=2, section_usd=15, stage_usd=60)
        per = 3 * (vfx.AUTHOR_SESSION_USD + vfx.REVIEW_USD)
        self.assertAlmostEqual(e["per_section_usd"], per)
        self.assertAlmostEqual(e["stage_usd"], 4 * per)
        self.assertEqual(e["rounds"], 3)

    def test_the_line_names_the_estimate_and_both_budgets(self):
        line = vfx.estimate_line(vfx.estimate_spend(2, 1, 8, 12))
        self.assertIn("estimate", line)
        self.assertIn("$8", line)
        self.assertIn("$12", line)

    def test_a_budget_under_the_estimate_is_said_to_end_sections_early(self):
        line = vfx.estimate_line(vfx.estimate_spend(2, 2, 5, 10))
        self.assertIn("best", line)


# --------------------------------------------------------------------------- #
# the planner, with fake engines
# --------------------------------------------------------------------------- #

class Costed(world.FakeEngine):
    """The stage's fake with money: a session costs what it is allowed, at most
    `author_cost`; a review costs `review_cost`. Kept apart from the shared fake."""

    author_cost = 3.0
    review_cost = 1.0
    caps: list = []
    log: list = []

    def __init__(self, config):
        super().__init__(config)
        self.usage.cost_usd = 0.0

    def stream(self, prompt, system=None, resume=False):
        writes = "Write" in tuple(self.config.tools or ())
        Costed.log.append("author" if writes else "review")
        if writes:
            Costed.caps.append(self.config.max_turns_budget_usd)
        events = list(super().stream(prompt, system=system, resume=resume))
        cap = self.config.max_turns_budget_usd
        self.usage.cost_usd += (min(self.author_cost, cap if cap is not None else 1e9)
                                if writes else self.review_cost)
        yield from events


class Marked(Costed):
    """An author that reads and thinks until it has spent most of its cap: its
    first session streams priced messages, and writes its program only when asked."""

    def stream(self, prompt, system=None, resume=False):
        writes = "Write" in tuple(self.config.tools or ())
        if writes and not resume:
            self.session_id = "author-session"
            Costed.caps.append(self.config.max_turns_budget_usd)
            for i in range(4):
                Costed.log.append(f"think{i}")
                yield message(f"m{i}", 170)
        yield from super().stream(prompt, system=system, resume=resume)


class Planned(unittest.TestCase):
    engine = Costed

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._engine = harness.ENGINE
        harness.ENGINE = self.engine
        world.FakeEngine.stream_calls, world.FakeEngine.json_calls = [], []
        world.FakeEngine.reviews, world.FakeEngine.docs = [], []
        world.FakeEngine.doc, world.FakeEngine.time_out_on = world.WORLD_DOC, 0
        world.FakeEngine.crash_on, world.FakeEngine.stale_frame = 0, False
        Costed.caps, Costed.log = [], []
        patcher = patch.dict(claude_engine.PRICES, PRICES)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.said: list[str] = []

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def planner(self, **kw) -> vfx.VfxPlanner:
        idea = Idea(title="T", genre="g", description="d")
        bundle = {"idea": idea, "outline": {}, "claims": [], "assets": [], "premise": ""}
        kw.setdefault("revisions", 2)
        planner = vfx.VfxPlanner(bundle, Dossier(self.root, idea, at=self.root),
                                 vfx.Workspace(self.root), model="sonnet",
                                 sections=[world.SECTION], author_bar=7.0, verbose=False, **kw)

        def say(msg):
            self.said.append(msg)
            Costed.log.append(f"say:{msg}")
        planner._say = say
        return planner

    def author(self, planner):
        group = {"section": "Open", "seconds": 15, "effects": world.EFFECTS, "cuts": []}
        with patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still",
                             lambda xml, at, **kw: (world.varied_png(), "rendered locally")), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return planner.author({"sections": [world.SECTION]}, [group], world.STYLE,
                                  {"ink": "#FFFFFF"})


class SectionBudget(Planned):
    def reviews(self, *scores):
        world.FakeEngine.reviews = [{"frames": [], "score": s, "summary": "s"} for s in scores]

    def test_a_section_stops_before_it_overruns_and_keeps_its_best_round(self):
        self.reviews(6, 4, 4)
        planner = self.planner(budget_usd=7, stage_budget_usd=70)
        doc = self.author(planner)[0]
        # round 0: 3 + 1 of 7; the revision gets 7 - 4 - 1 (the review it still owes) = 2
        self.assertEqual(Costed.caps, [4.0, 2.0])
        self.assertEqual([r["score"] for r in doc["rounds"]], [6, 4])
        self.assertLessEqual(planner.spend.spent(1), 7 + 1e-9)
        self.assertEqual(doc["budget"]["stopped"], "budget")
        self.assertEqual(doc["best_round"], 0)          # the section ends on its best reviewed round
        self.assertEqual(doc["score"], 6)

    def test_a_section_that_is_afforded_all_its_rounds_is_not_stopped(self):
        self.reviews(5, 6, 8)
        planner = self.planner(budget_usd=40, stage_budget_usd=400)
        doc = self.author(planner)[0]
        self.assertEqual([r["score"] for r in doc["rounds"]], [5, 6, 8])
        self.assertIsNone(doc["budget"]["stopped"])

    def test_the_stage_budget_stops_a_section_too(self):
        self.reviews(5, 5, 5)
        planner = self.planner(budget_usd=100, stage_budget_usd=6)
        doc = self.author(planner)[0]
        self.assertEqual(Costed.caps, [4.0, 1.0])
        self.assertEqual(doc["budget"]["stopped"], "budget")
        self.assertLessEqual(planner.spend.stage_spent(), 6 + 1e-9)

    def test_a_session_is_capped_by_the_budget_it_is_granted(self):
        self.reviews(9)
        self.author(self.planner(budget_usd=3, stage_budget_usd=30))
        self.assertEqual(Costed.caps, [1.0])             # 3 - the review it owes (2, unmeasured)

    def test_a_stage_with_nothing_left_starts_no_author(self):
        planner = self.planner(budget_usd=10, stage_budget_usd=0.5)
        doc = self.author(planner)[0]
        self.assertNotIn("author", Costed.log)
        self.assertTrue(any("budget" in f for f in doc["findings"]))

    def test_the_run_reports_each_section_and_the_stage_against_their_budgets(self):
        self.reviews(9)
        planner = self.planner(budget_usd=10, stage_budget_usd=25)
        doc = self.author(planner)[0]
        row = doc["budget"]
        self.assertEqual(row["budget_usd"], 10)
        self.assertAlmostEqual(row["spent_usd"], 4.0)
        self.assertAlmostEqual(row["author_usd"], 3.0)
        self.assertAlmostEqual(row["review_usd"], 1.0)
        report = planner.spend.report()
        self.assertEqual(report["stage"]["budget_usd"], 25)
        self.assertAlmostEqual(report["stage"]["spent_usd"], 4.0)
        spend_lines = [m for m in self.said if "spent" in m and "$" in m]
        self.assertTrue(any("section 1" in m and "$10" in m for m in spend_lines))
        self.assertTrue(any("stage" in m and "$25" in m for m in spend_lines))

    def test_the_stage_budget_defaults_to_the_sections_budgets(self):
        self.reviews(9)
        planner = self.planner(budget_usd=10)
        self.author(planner)
        self.assertEqual(planner.spend.report()["stage"]["budget_usd"], 10)

    def test_the_estimate_is_printed_before_the_first_agent_starts(self):
        self.reviews(9)
        self.author(self.planner(budget_usd=10, stage_budget_usd=25))
        first_agent = min(i for i, e in enumerate(Costed.log) if not e.startswith("say:"))
        estimates = [i for i, e in enumerate(Costed.log) if e.startswith("say:") and "estimate" in e]
        self.assertTrue(estimates)
        self.assertLess(estimates[0], first_agent)

    def test_the_author_is_told_the_budget_of_each_session(self):
        self.reviews(5, 9)
        self.author(self.planner(budget_usd=40, stage_budget_usd=400))
        prompts = [c["prompt"] for c in world.FakeEngine.stream_calls if "Write" in c["tools"]]
        self.assertIn(sdkkit.budget_line(4.0), prompts[0])
        self.assertIn(sdkkit.budget_line(4.0), prompts[1])


class NearTheBudget(Planned):
    engine = Marked

    def test_an_author_that_spends_most_of_its_cap_ends_on_a_build_a_still_and_a_review(self):
        world.FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "ok"}]
        planner = self.planner(budget_usd=15, stage_budget_usd=60)
        doc = self.author(planner)[0]
        calls = [c for c in world.FakeEngine.stream_calls if "Write" in c["tools"]]
        # the session wrote nothing before its mark; the one call that reached the
        # fake's program writer is the finish, resumed
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["resume"])
        self.assertIn(sdkkit.finish_prompt("", "")[:30], calls[0]["prompt"])
        self.assertIn("kit.py build", calls[0]["prompt"])
        # the session was stopped at its mark (two messages, $3.40 of $4), not run to its cap
        self.assertEqual([e for e in Costed.log if e.startswith("think")], ["think0", "think1"])
        # what the finish call may spend is what the session left of its cap
        self.assertAlmostEqual(Costed.caps[0], 4.0)
        self.assertAlmostEqual(Costed.caps[1], 4.0 - 2 * 1.7, places=6)
        # a built document, a reviewed round: no exit 1, no unreviewed final round
        self.assertEqual(doc["rounds"][0]["errors"], 0)
        self.assertEqual(doc["rounds"][0]["score"], 9)
        self.assertEqual(doc["symbols"], ["world-open"])
        self.assertAlmostEqual(doc["budget"]["timed_out_usd"], 3.4, places=6)
        self.assertLessEqual(planner.spend.spent(1), 15)


# --------------------------------------------------------------------------- #
# AUTO-68: a round the host lost is never charged to the author
# --------------------------------------------------------------------------- #

class InfraRound(Planned):
    """A round whose only blockers are frames the host could not render is
    gated again after a bounded wait, without a session, and the host's
    failure is recorded on the round; no revision is paid for it."""

    LOST = "render host unreachable: ssh: connect to host root@small port 22: Connection refused"

    def gated(self, planner, still):
        group = {"section": "Open", "seconds": 15, "effects": world.EFFECTS, "cuts": []}
        with patch.object(vfx, "INFRA_WAIT", 0.0), \
                patch.object(vfx.render, "engine_validate", lambda data, **kw: []), \
                patch.object(vfx.render, "engine_render_still", still), \
                patch.object(vfx, "encode_findings", lambda *a, **k: []):
            return planner.author({"sections": [world.SECTION]}, [group], world.STYLE,
                                  {"ink": "#FFFFFF"})[0]

    def test_a_round_only_the_host_lost_is_regated_without_a_session_and_not_charged(self):
        world.FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "never asked"}]
        gates: list[float] = []

        def still(xml, at, **kw):
            gates.append(at)
            return None, self.LOST
        planner = self.planner(budget_usd=20, stage_budget_usd=200)
        doc = self.gated(planner, still)
        self.assertEqual(Costed.log.count("author"), 1)            # no paid revision
        self.assertNotIn("review", Costed.log)                      # nothing to look at
        self.assertAlmostEqual(planner.spend.spent(1), 3.0)         # one session, nothing else
        self.assertEqual(doc["revisions"], 0)
        self.assertEqual(len(doc["rounds"]), 1)
        self.assertTrue(doc["rounds"][-1]["infra"])                 # recorded as the host's
        self.assertTrue(all(f.startswith("infra") for f in doc["rounds"][-1]["infra"]))
        self.assertFalse([f for f in doc["findings"] if f.startswith("error")])
        self.assertTrue([f for f in doc["findings"] if f.startswith("infra")])
        self.assertIsNone(doc["score"])
        frames = len(vfx.shot_times(world.SECTION["shots"], 15))
        self.assertEqual(len(gates), 2 * frames)                    # gated once, then again
        self.assertTrue(any("gating again" in s for s in self.said), self.said)

    def test_a_host_back_after_the_wait_gets_the_round_its_review(self):
        world.FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "fine"}]
        passes: list[float] = []

        def still(xml, at, **kw):
            passes.append(at)
            if len(passes) <= len(vfx.shot_times(world.SECTION["shots"], 15)):
                return None, self.LOST                              # the first gate's frames
            return world.varied_png(), "rendered on root@small"
        planner = self.planner(budget_usd=20, stage_budget_usd=200)
        doc = self.gated(planner, still)
        self.assertEqual(Costed.log.count("author"), 1)
        self.assertEqual(Costed.log.count("review"), 1)
        self.assertEqual(doc["score"], 9)
        self.assertEqual(doc["rounds"][0]["score"], 9)
        self.assertTrue(doc["rounds"][0]["infra"])                  # what the first pass lost
        self.assertFalse([f for f in doc["findings"] if f.startswith(("error", "infra"))])

    def test_a_world_too_heavy_for_the_still_deadline_gets_its_revision_not_a_wait(self):
        # Both hosts stop the still for its cost: the world is the author's to
        # lighten, so the round is an error, revised, and never the host's.
        world.FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "never asked"}]
        timed_out = "render host unreachable: Command '[...]' timed out after 600.0 seconds"
        planner = self.planner(budget_usd=20, stage_budget_usd=200, revisions=1)
        doc = self.gated(planner, lambda xml, at, **kw: (None, timed_out))
        self.assertEqual(doc["revisions"], 1)
        self.assertEqual(Costed.log.count("author"), 2)
        self.assertNotIn("review", Costed.log)
        self.assertFalse(doc["rounds"][0]["infra"])
        self.assertTrue([f for f in doc["findings"] if f.startswith("error")])
        self.assertFalse([f for f in doc["findings"] if f.startswith("infra")])
        self.assertFalse(any("gating again" in s for s in self.said), self.said)
        revision = [c for c in world.FakeEngine.stream_calls if "Write" in c["tools"]][1]
        self.assertIn("Cost drivers of this document", revision["prompt"])
        self.assertNotIn("infra:", revision["prompt"])

    def test_an_authors_error_beside_a_lost_frame_is_still_a_revision_without_the_infra_lines(self):
        world.FakeEngine.reviews = [{"frames": [], "score": 9, "summary": "fine"}] * 2
        n = {"count": 0}

        def still(xml, at, **kw):
            n["count"] += 1
            if n["count"] == 1:
                return None, self.LOST
            if n["count"] == 2:
                return None, "render failed\nerror[E7]: the camera frames nothing"
            return world.varied_png(), "rendered on root@small"
        planner = self.planner(budget_usd=20, stage_budget_usd=200)
        doc = self.gated(planner, still)
        self.assertEqual(doc["revisions"], 1)
        revision = [c for c in world.FakeEngine.stream_calls if "Write" in c["tools"]][1]
        self.assertIn("the camera frames nothing", revision["prompt"])
        self.assertNotIn("infra:", revision["prompt"])


# --------------------------------------------------------------------------- #
# the shared round
# --------------------------------------------------------------------------- #

class MeteredEngine:
    """Streams scripted calls of priced messages; records the cap of each call."""

    def __init__(self, calls: list, cwd: Path, model: str = "fake"):
        self.config = SimpleNamespace(timeout=3600.0, env={}, max_turns_budget_usd=None, cwd=cwd,
                                      model=model)
        self.calls, self.script, self.model = [], list(calls), model
        self.session_id = None
        self.usage = Usage()
        self.emitted = 0
        self.budget_file = []

    def stream(self, prompt, system=None, resume=False):
        self.calls.append({"prompt": prompt, "resume": resume,
                           "cap": self.config.max_turns_budget_usd})
        self.session_id = "s"
        for i, tokens in enumerate(self.script.pop(0)):
            self.emitted += 1
            yield message(f"m{len(self.calls)}-{i}", tokens, self.model)
            path = (self.config.env or {}).get(sdkkit.BUDGET_FILE_VAR)
            if path and Path(path).is_file():
                self.budget_file.append(Path(path).read_text())


class CliBudgetEngine:
    """An unpriced model under the CLI's own budget: a session stops with the
    `error_max_budget_usd` result at its cap, as the claude CLI does (the result
    event, then exit 1), and the engine books the result's cost once."""

    def __init__(self, cwd: Path, raises: bool = True, model: str = "claude-opus-5-5"):
        self.config = SimpleNamespace(timeout=3600.0, env={}, max_turns_budget_usd=None,
                                      cwd=cwd, model=model)
        self.calls, self.raises, self.model = [], raises, model
        self.session_id = None
        self.usage = Usage()

    def result(self, subtype: str, cost: float) -> Event:
        self.usage.add({"total_cost_usd": cost})
        return Event(type="result", subtype=subtype, raw={
            "type": "result", "subtype": subtype, "total_cost_usd": cost,
            "is_error": subtype != "success"})

    def stream(self, prompt, system=None, resume=False):
        cap = self.config.max_turns_budget_usd
        self.calls.append({"prompt": prompt, "resume": resume, "cap": cap})
        self.session_id = "s"
        yield message(f"c{len(self.calls)}", 100, self.model)
        if len(self.calls) == 1:
            yield self.result("error_max_budget_usd", 3.3)
            if self.raises:
                raise claude_engine.ClaudeExecutionError("claude exited with code 1", returncode=1)
        else:
            yield self.result("success", 0.4)


class UnpricedBudgetMark(unittest.TestCase):
    """No price table: the mark is the CLI's budget, 80% of the grant, then the rest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict(claude_engine.PRICES, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_round(self, engine, cap=4.0, **kw):
        return list(sdkkit.stream_round(engine, "do it", system="sys", budget_usd=cap, **kw))

    def test_the_first_session_runs_under_80_percent_of_the_grant(self):
        engine = CliBudgetEngine(self.cwd)
        self.run_round(engine)
        self.assertAlmostEqual(engine.calls[0]["cap"], 3.2)

    def test_a_session_stopped_by_the_cli_budget_is_resumed_once_with_the_rest(self):
        engine = CliBudgetEngine(self.cwd)
        events = self.run_round(engine, finish="FINISH build and render")
        self.assertEqual(len(engine.calls), 2)
        second = engine.calls[1]
        self.assertTrue(second["resume"])
        self.assertIn("FINISH build and render", second["prompt"])
        self.assertAlmostEqual(second["cap"], 4.0 - 3.3)     # the grant less the reported cost
        self.assertEqual(engine.config.max_turns_budget_usd, 4.0)   # restored
        self.assertEqual([e.subtype for e in events if e.type == "result"],
                         ["error_max_budget_usd", "success"])

    def test_the_stopped_sessions_cost_is_counted_once(self):
        engine = CliBudgetEngine(self.cwd)
        self.run_round(engine)
        self.assertAlmostEqual(engine.usage.cost_usd, 3.3 + 0.4)
        self.assertEqual(engine.usage.timed_out_calls, 0)

    def test_the_stop_without_an_exit_error_is_resumed_too(self):
        engine = CliBudgetEngine(self.cwd, raises=False)
        self.run_round(engine)
        self.assertEqual(len(engine.calls), 2)

    def test_the_default_finish_asks_for_the_build_and_a_still(self):
        engine = CliBudgetEngine(self.cwd)
        self.run_round(engine)
        self.assertIn("kit.py build", engine.calls[1]["prompt"])
        self.assertIn("frame", engine.calls[1]["prompt"])

    def test_with_too_little_left_the_stop_is_raised(self):
        engine = CliBudgetEngine(self.cwd)
        with self.assertRaises(claude_engine.ClaudeError):
            self.run_round(engine, cap=3.4)                  # 3.3 spent, 0.1 left
        self.assertEqual(len(engine.calls), 1)

    def test_a_failure_that_is_not_the_budget_is_raised(self):
        engine = CliBudgetEngine(self.cwd)
        engine.result = lambda subtype, cost: Event(
            type="result", subtype="error_during_execution",
            raw={"type": "result", "subtype": "error_during_execution", "total_cost_usd": 1.0})
        with self.assertRaises(claude_engine.ClaudeError):
            self.run_round(engine)
        self.assertEqual(len(engine.calls), 1)

    def test_a_priced_model_keeps_its_streaming_meter_and_the_full_cap(self):
        with patch.dict(claude_engine.PRICES, PRICES):
            engine = MeteredEngine([[100, 100]], self.cwd)
            engine.config.model = "fake"
            self.run_round(engine)
        self.assertEqual(engine.calls[0]["cap"], 4.0)


class BudgetMark(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        patcher = patch.dict(claude_engine.PRICES, PRICES)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_round(self, engine, cap, **kw):
        return list(sdkkit.stream_round(engine, "do it", system="sys", budget_usd=cap, **kw))

    def test_the_mark_is_80_percent_of_the_session_cap(self):
        self.assertEqual(sdkkit.BUDGET_CUTOFF, 0.8)

    def test_a_session_that_stays_under_the_mark_is_one_call_with_its_cap(self):
        engine = MeteredEngine([[100, 100]], self.cwd)
        self.run_round(engine, 4.0)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["cap"], 4.0)

    def test_a_session_at_the_mark_is_stopped_and_asked_for_its_build_and_still(self):
        engine = MeteredEngine([[150, 100, 100, 100, 100], [20]], self.cwd)
        self.run_round(engine, 4.0, finish="FINISH build and render")
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(engine.emitted, 3 + 1)           # stopped at the third message, then the finish
        second = engine.calls[1]
        self.assertTrue(second["resume"])
        self.assertIn("FINISH build and render", second["prompt"])
        self.assertAlmostEqual(second["cap"], 4.0 - 3.5)  # the rest of the cap is the finish's
        self.assertEqual(engine.config.max_turns_budget_usd, 4.0)   # restored

    def test_what_the_stopped_session_spent_is_booked_as_a_cut_off_call(self):
        engine = MeteredEngine([[150, 100, 100, 100], [20]], self.cwd)
        self.run_round(engine, 4.0)
        self.assertEqual(engine.usage.timed_out_calls, 1)
        self.assertAlmostEqual(engine.usage.timed_out_cost_usd, 3.5)
        self.assertAlmostEqual(engine.usage.cost_usd, 3.5)
        self.assertTrue(engine.usage.timed_out_cost_estimated)

    def test_the_default_finish_asks_for_the_build_and_a_still(self):
        engine = MeteredEngine([[350], [20]], self.cwd)
        self.run_round(engine, 4.0)
        self.assertIn("kit.py build", engine.calls[1]["prompt"])
        self.assertIn("frame", engine.calls[1]["prompt"])

    def test_with_too_little_left_there_is_no_finish_call(self):
        engine = MeteredEngine([[90, 100]], self.cwd)
        self.run_round(engine, 1.0)                         # the mark (0.8) is met, 0.1 is left
        self.assertEqual(len(engine.calls), 1)

    def test_an_unpriced_model_is_not_metered_on_the_stream(self):
        engine = MeteredEngine([[500, 500]], self.cwd, model="unknown")
        self.run_round(engine, 4.0)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.emitted, 2)             # never stopped by the stream
        self.assertAlmostEqual(engine.calls[0]["cap"], 3.2)   # the CLI's budget is the mark

    def test_without_a_budget_the_round_is_as_it_was(self):
        engine = MeteredEngine([[500, 500]], self.cwd)
        list(sdkkit.stream_round(engine, "do it", system="sys"))
        self.assertEqual(len(engine.calls), 1)
        self.assertIsNone(engine.calls[0]["cap"])
        self.assertNotIn(sdkkit.BUDGET_FILE_VAR, engine.config.env)

    def test_the_kit_is_told_what_is_left_the_way_it_is_told_the_time(self):
        engine = MeteredEngine([[100, 100, 50]], self.cwd)
        self.run_round(engine, 4.0)
        path = engine.config.env[sdkkit.BUDGET_FILE_VAR]
        self.assertTrue(Path(path).is_relative_to(self.cwd))
        env = {sdkkit.BUDGET_FILE_VAR: path}
        self.assertIn("budget left: $1.50", sdkkit.budget_left_line(env))
        self.assertEqual(sdkkit.budget_left_line({}), "")
        self.assertEqual(sdkkit.budget_left_line({sdkkit.BUDGET_FILE_VAR: str(self.cwd / "none")}), "")
        shown = io.StringIO()
        with patch.dict(os.environ, env), contextlib.redirect_stdout(shown):
            sdkkit._print_time_left()
        self.assertIn("budget left", shown.getvalue())

    def test_the_budget_line_states_the_cap_and_the_mark(self):
        line = sdkkit.budget_line(4.0)
        self.assertIn("$4", line)
        self.assertIn("80%", line)
        self.assertIn("build", line)


if __name__ == "__main__":
    unittest.main()
