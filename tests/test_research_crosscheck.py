"""AUTO-134: `yt research --crosscheck N` checks key claims against sources
the research did not cite, judged by a reviewer of another family.

Every fact-check checked the script against the dossier the research wrote
itself, and each claim rested on the one page its own pass found. The new
stage, `09-crosscheck`, runs after the outline: it picks up to N claims (the
outline's first, then the weak ones, then the high ones with a number), runs
one web pass per claim asking for sources independent of the cited host, and
has the checker -- never the research model -- decide whether they
corroborate, contradict or dispute it. A verdict counts only on URLs its own
pass fetched and on words its notes record; otherwise it is asked once more
and then recorded as unverified, never counted.

No model, network or render host is reached: the web engine is a fake that
streams the events a test gives it, and both harnesses answer by name.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from claude_engine import ClaudeError, Event, Usage  # noqa: E402
from ytstudio import assets as yt_assets  # noqa: E402
from ytstudio import cli, harness, research, reviewers, spend  # noqa: E402
from ytstudio.core import Idea  # noqa: E402

from test_research_levels import RAW  # noqa: E402

CITED = "https://www.nasa.gov/comets/tail"
OTHER = "https://www.independent.org/a"

CLAIMS = [
    # 1: on the outline
    {"claim": "The tail is 100 million km long.", "detail": "the ion tail",
     "number": "100 million km", "source_title": "NASA", "source_url": CITED,
     "source_kind": "primary", "confidence": "high"},
    # 2: weak (medium, secondary)
    {"claim": "Tails always point away from the Sun.", "detail": "",
     "source_title": "A blog", "source_url": "https://example.org/away",
     "source_kind": "secondary", "confidence": "medium"},
    # 3: high, primary, with a number
    {"claim": "The nucleus is 10 km across.", "detail": "", "number": "10 km",
     "source_title": "ESA", "source_url": "https://esa.int/nucleus",
     "source_kind": "primary", "confidence": "high"},
    # 4: high, primary, no number: never a target
    {"claim": "Comets are dirty snowballs.", "detail": "", "source_title": "ESA",
     "source_url": "https://esa.int/snow", "source_kind": "primary", "confidence": "high"},
    # 5: caveated
    {"claim": "A tail glows by fluorescence.", "detail": "", "source_title": "ESA",
     "source_url": "https://esa.int/glow", "source_kind": "primary", "confidence": "high",
     "caveat": "only the ion tail"},
]

OUTLINE = {"working_title": "Comets", "runtime": "10 min",
           "sections": [{"heading": "Tail", "seconds": 60, "beats": ["b"],
                         "claims_used": ["  the TAIL is 100 million km long. "]}],
           "misconceptions": [], "identifiable_case": {"who": "", "source": ""},
           "human_stories": [], "parallels": [], "parallels_why": ""}

SAYS = "The tail stretches 100 million km."


def fetch(url):
    return Event(type="assistant", subtype="tool_use", tool_name="WebFetch",
                 tool_input={"url": url})


def search(query):
    return Event(type="assistant", subtype="tool_use", tool_name="WebSearch",
                 tool_input={"query": query})


def said(text):
    return Event(type="assistant", text=text)


def done(denied=()):
    return Event(type="result", permission_denials=list(denied))


def blank(schema):
    props = schema.get("properties", {})
    empty = {"array": list, "object": dict, "integer": int}
    return {k: empty.get(props.get(k, {}).get("type"), str)() for k in schema.get("required", [])}


def corroborated(url=OTHER.replace("www.", "") + "/", says=SAYS):
    return {"status": "corroborated", "sources": [{"url": url, "says": says, "agrees": "yes"}],
            "correction": "", "note": "an independent page agrees"}


class FakeEngine:
    """Streams what `script(prompt)` returns; records each pass's prompt."""

    script = staticmethod(lambda prompt: [])
    calls: list[str] = []
    barrier: threading.Barrier | None = None

    def __init__(self, config):
        self.config = config
        self.usage = Usage()

    def stream(self, prompt, system=None):
        FakeEngine.calls.append(prompt)
        if FakeEngine.barrier is not None:
            FakeEngine.barrier.wait()
        yield from FakeEngine.script(prompt)


class Answers:
    """A harness that answers by name (a callable is handed the prompt),
    else blank, and records when each decision starts and ends."""

    def __init__(self, answers=None, events=None, model="sonnet"):
        self.answers = answers or {}
        self.prompts: dict[str, str] = {}
        self.events = events if events is not None else []
        self.usage = Usage()
        self.model = model
        self._lock = threading.Lock()

    def decide(self, name, prompt, schema):
        with self._lock:
            self.events.append(("start", name))
            self.prompts[name] = prompt
        answer = self.answers.get(name)
        if callable(answer):
            answer = answer(prompt)
        with self._lock:
            self.events.append(("end", name))
        return copy.deepcopy(answer) if answer is not None else blank(schema)

    def forget(self, name):
        pass

    def report(self):
        return {"model": self.model,
                "decisions": [{"name": n, "kind": "decision"} for w, n in self.events
                              if w == "end" and n in self.prompts]}


def default_pass(prompt):
    return [search("comet tail length"), fetch(OTHER),
            said(f"{OTHER}\n{SAYS}"), done()]


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()
        for name in ("find_assets", "cited_papers"):
            p = mock.patch.object(yt_assets, name, side_effect=lambda *a, **k: [])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(research, "ClaudeEngine", FakeEngine)
        p.start()
        self.addCleanup(p.stop)
        FakeEngine.calls = []
        FakeEngine.barrier = None
        FakeEngine.script = staticmethod(default_pass)
        self.idea = Idea(title="Comets", genre="explainer", description="What a tail is.")
        self.events: list = []

    def researcher(self, *, crosscheck=3, idea=None, claims=CLAIMS, checks=None,
                   jobs=1, **kw):
        idea = idea or self.idea
        dossier = research.Dossier(self.root, idea, at=self.root)
        r = research.Researcher(idea, dossier, verbose=False, jobs=jobs,
                                crosscheck=crosscheck, **kw)
        self.harness = r.harness = Answers(
            {"03-claims": {"claims": copy.deepcopy(claims)}, "07-outline": OUTLINE},
            self.events)
        if r.checker is not None:
            self.checker = r.checker = Answers(
                checks if checks is not None else
                {f"09-crosscheck-{i}": corroborated() for i in range(1, 9)},
                self.events, model="opus")
        return r

    def raw(self):
        return sorted(p.stem for p in (self.root / "raw").iterdir())

    def crosscheck_passes(self):
        return [p for p in FakeEngine.calls if "CLAIM UNDER CHECK" in p]


# --------------------------------------------------------------------------- #
# which claims, and the cache
# --------------------------------------------------------------------------- #

class Targets(unittest.TestCase):
    def test_outline_claims_then_weak_ones_then_high_ones_with_a_number(self):
        picked = research.crosscheck_targets(CLAIMS, OUTLINE, {}, 10)
        self.assertEqual([c["claim"] for c in picked],
                         [CLAIMS[0]["claim"], CLAIMS[1]["claim"], CLAIMS[4]["claim"],
                          CLAIMS[2]["claim"]])

    def test_the_list_is_capped_at_the_limit(self):
        picked = research.crosscheck_targets(CLAIMS, OUTLINE, {}, 2)
        self.assertEqual([c["claim"] for c in picked], [CLAIMS[0]["claim"], CLAIMS[1]["claim"]])
        self.assertEqual(research.crosscheck_targets(CLAIMS, OUTLINE, {}, 0), [])

    def test_unsourced_and_low_claims_are_weak_and_keep_dossier_order(self):
        rows = [dict(CLAIMS[3], claim="A", confidence="low"),
                dict(CLAIMS[3], claim="B", source_kind="unsourced"),
                dict(CLAIMS[2], claim="C")]
        picked = research.crosscheck_targets(rows, {}, {}, 5)
        self.assertEqual([c["claim"] for c in picked], ["A", "B", "C"])


class Stage(_Case):
    def test_the_cross_check_starts_only_after_the_outline_ends(self):
        self.researcher().run()
        ends = [n for n, (w, name) in enumerate(self.events) if (w, name) == ("end", "07-outline")]
        starts = [n for n, (w, name) in enumerate(self.events)
                  if w == "start" and name == "09-crosscheck-1"]
        self.assertTrue(ends and starts, self.events)
        self.assertLess(ends[0], starts[0])

    def test_raw_gains_the_stage_and_its_targets_only_when_asked(self):
        self.researcher().run()
        self.assertEqual(self.raw(), sorted(RAW + ["09-crosscheck", "09-crosscheck-targets"]))
        targets = json.loads((self.root / "raw" / "09-crosscheck-targets.json").read_text())
        self.assertEqual(targets, sorted(research.claim_key(c["claim"])
                                         for c in (CLAIMS[0], CLAIMS[1], CLAIMS[4])))

    def test_off_by_default_nothing_changes(self):
        dossier = research.Dossier(self.root, self.idea, at=self.root)
        r = research.Researcher(self.idea, dossier, verbose=False, jobs=1)
        self.assertIsNone(r.checker)
        r.harness = Answers({"03-claims": {"claims": copy.deepcopy(CLAIMS)},
                             "07-outline": OUTLINE}, self.events)
        data = r.run()
        self.assertEqual(self.raw(), RAW)
        self.assertEqual(FakeEngine.calls, [])
        self.assertFalse(any(n.startswith("09-") for _, n in self.events))
        self.assertNotIn("crosscheck", data)
        self.assertNotIn("crosscheck_skipped", data)
        self.assertEqual(data["usage"], spend.from_usage(r.usage, r.model))
        self.assertEqual(data["engine"], r.harness.report())
        self.assertNotIn("independent", json.dumps(data["claims"]))

    def test_a_wordless_film_skips_it_and_says_so(self):
        idea = Idea(title="Comets", genre="cinematic", description="No narration.")
        data = self.researcher(idea=idea).run()
        self.assertEqual(data["crosscheck_skipped"], "cinematic scope")
        self.assertNotIn("crosscheck", data)
        self.assertEqual(self.crosscheck_passes(), [])
        self.assertNotIn("09-crosscheck", self.raw())

    def test_the_same_targets_replay_and_changed_claims_check_again(self):
        self.researcher().run()
        self.assertEqual(len(self.crosscheck_passes()), 3)
        FakeEngine.calls = []
        self.events.clear()
        self.researcher().run()
        self.assertEqual(self.crosscheck_passes(), [])
        # new claims: the cached 03-claims is dropped, so the targets differ
        (self.root / "raw" / "03-claims.json").unlink()
        changed = [dict(c) for c in CLAIMS]
        changed[1]["claim"] = "Tails mostly point away from the Sun."
        self.researcher(claims=changed).run()
        self.assertEqual(len(self.crosscheck_passes()), 3)
        self.assertTrue(any("Tails mostly point" in p for p in self.crosscheck_passes()))


# --------------------------------------------------------------------------- #
# the web passes
# --------------------------------------------------------------------------- #

class Passes(_Case):
    def test_the_passes_run_jobs_wide(self):
        FakeEngine.barrier = threading.Barrier(3, timeout=5)
        data = self.researcher(jobs=3).run()
        self.assertEqual(len(self.crosscheck_passes()), 3)
        self.assertEqual([e["status"] for e in data["crosscheck"]], ["corroborated"] * 3)

    def test_a_failed_pass_is_unverified_and_the_others_finish(self):
        def script(prompt):
            if "always point away" in prompt:
                raise ClaudeError("the web pass timed out")
            return default_pass(prompt)
        FakeEngine.script = staticmethod(script)
        data = self.researcher().run()
        by = {e["claim"]: e for e in data["crosscheck"]}
        failed = by[CLAIMS[1]["claim"]]
        self.assertEqual(failed["status"], "unverified")
        self.assertTrue(any("timed out" in p for p in failed["problems"]))
        self.assertEqual(by[CLAIMS[0]["claim"]]["status"], "corroborated")
        self.assertEqual(by[CLAIMS[4]["claim"]]["status"], "corroborated")

    def test_a_failed_verdict_is_unverified_and_the_others_finish(self):
        def down(prompt):
            raise harness.HarnessError("[research] 09-crosscheck-1: the reviewer is down")
        checks = {f"09-crosscheck-{i}": corroborated() for i in range(1, 4)}
        checks["09-crosscheck-1"] = down
        data = self.researcher(checks=checks).run()
        by = {e["claim"]: e for e in data["crosscheck"]}
        failed = by[CLAIMS[0]["claim"]]
        self.assertEqual(failed["status"], "unverified")
        self.assertTrue(any(p.startswith(research.VERDICT_FAILED) and "reviewer is down" in p
                            for p in failed["problems"]), failed)
        self.assertEqual(by[CLAIMS[1]["claim"]]["status"], "corroborated")
        self.assertEqual(by[CLAIMS[4]["claim"]]["status"], "corroborated")
        self.assertTrue((self.root / "raw" / "09-crosscheck.json").is_file())

    def test_a_failed_second_ask_is_unverified_with_both_problems(self):
        def down(prompt):
            raise ClaudeError("budget spent")
        data = self.researcher(crosscheck=1, checks={
            "09-crosscheck-1": corroborated(url="https://never-fetched.org/x"),
            "09-crosscheck-1-again": down}).run()
        entry = data["crosscheck"][0]
        self.assertEqual(entry["status"], "unverified")
        text = " ".join(entry["problems"])
        self.assertIn("https://never-fetched.org/x", text)
        self.assertIn("budget spent", text)

    def test_a_rerun_asks_again_only_what_failed(self):
        def script(prompt):
            if "always point away" in prompt:
                raise ClaudeError("the web pass timed out")
            return default_pass(prompt)
        FakeEngine.script = staticmethod(script)

        def down(prompt):
            raise harness.HarnessError("the reviewer is down")
        checks = {f"09-crosscheck-{i}": corroborated() for i in range(1, 4)}
        checks["09-crosscheck-1"] = down
        self.researcher(checks=checks).run()
        FakeEngine.script = staticmethod(default_pass)
        FakeEngine.calls = []
        self.events.clear()
        data = self.researcher().run()
        # claim 2's pass is run again; claim 1's verdict is asked again on
        # the pass it kept; claim 3 replays
        passes = self.crosscheck_passes()
        self.assertEqual(len(passes), 1)
        self.assertIn("always point away", passes[0])
        asked = sorted(n for w, n in self.events if w == "start" and n.startswith("09-"))
        self.assertEqual(asked, ["09-crosscheck-1", "09-crosscheck-2"])
        self.assertEqual([e["status"] for e in data["crosscheck"]], ["corroborated"] * 3)
        saved = json.loads((self.root / "raw" / "09-crosscheck.json").read_text())
        self.assertEqual([e["status"] for e in saved], ["corroborated"] * 3)

    def test_a_verdict_rejected_twice_is_not_asked_again_on_a_rerun(self):
        bad = corroborated(url="https://never-fetched.org/x")
        self.researcher(crosscheck=1, checks={"09-crosscheck-1": bad,
                                              "09-crosscheck-1-again": bad}).run()
        FakeEngine.calls = []
        self.events.clear()
        data = self.researcher(crosscheck=1).run()
        self.assertEqual(self.crosscheck_passes(), [])
        self.assertFalse(any(n.startswith("09-") for _, n in self.events))
        self.assertEqual(data["crosscheck"][0]["status"], "unverified")

    def test_the_pass_names_the_cited_host_and_asks_for_independent_pages(self):
        self.researcher(crosscheck=1).run()
        prompt = self.crosscheck_passes()[0]
        self.assertIn(CLAIMS[0]["claim"], prompt)
        self.assertIn("100 million km", prompt)
        self.assertIn("nasa.gov", prompt)
        self.assertIn("Open the pages", prompt)
        self.assertIn("under its URL", prompt)


# --------------------------------------------------------------------------- #
# the checker
# --------------------------------------------------------------------------- #

class Checker(_Case):
    def test_the_verdict_goes_to_the_checker_never_the_research_harness(self):
        r = self.researcher(crosscheck=1)
        self.assertEqual(r.checker_model, "opus")
        r.run()
        self.assertIn("09-crosscheck-1", self.checker.prompts)
        self.assertFalse(any(n.startswith("09-") for n in self.harness.prompts))
        prompt = self.checker.prompts["09-crosscheck-1"]
        # the pass's notes for each URL sit next to that URL
        self.assertIn(OTHER, prompt)
        self.assertIn(SAYS, prompt)

    def test_the_checker_is_built_with_the_reviewer_and_its_role(self):
        built = []
        real = harness.Harness

        def record(*a, **kw):
            built.append((a, kw))
            return real(*a, **kw)
        with mock.patch.object(research.harness, "Harness", side_effect=record):
            dossier = research.Dossier(self.root, self.idea, at=self.root)
            research.Researcher(self.idea, dossier, verbose=False, crosscheck=2,
                                reviewer_model="opus", model="sonnet")
        checker = [kw for a, kw in built if kw.get("role") == research.CROSSCHECK_ROLE]
        self.assertEqual(len(checker), 1, built)
        self.assertEqual(checker[0]["model"], "opus")
        self.assertNotIn("engine", checker[0])

    def test_a_pool_reviewer_builds_the_checker_on_the_gateway(self):
        built = []
        pooled = reviewers.Resolved(model="zai/glm-5.3", maker="zai", providers=("zai",),
                                    has=reviewers.HAS, price={"input": 1.0, "output": 2.0},
                                    price_source="pinned", fetched_at="")
        with mock.patch.object(research.harness, "Harness",
                               side_effect=lambda *a, **kw: built.append(kw) or mock.Mock()):
            dossier = research.Dossier(self.root, self.idea, at=self.root)
            r = research.Researcher(self.idea, dossier, verbose=False, crosscheck=2,
                                    reviewer_model="sdk:zai/glm-5.3", pooled=pooled)
        checker = [kw for kw in built if kw.get("role") == research.CROSSCHECK_ROLE]
        self.assertEqual(checker[0]["engine"], "gateway")
        self.assertEqual(checker[0]["model"], "zai/glm-5.3")
        self.assertEqual(checker[0]["gateway"]["providers"], ["zai"])
        self.assertEqual(r.checker_model, "zai/glm-5.3")

    def test_the_record_prices_the_checker_at_the_reviewers_rate(self):
        r = self.researcher(crosscheck=1)
        self.harness.usage = Usage(cost_usd=1.0, calls=3,
                                   by_model={"claude-sonnet-5": {"cost_usd": 1.0}})
        self.checker.usage = Usage(cost_usd=0.25, calls=1,
                                   by_model={"claude-opus-5-5": {"cost_usd": 0.25}})
        data = r.run()
        self.assertAlmostEqual(data["usage"]["cost_usd"], 1.25)
        self.assertEqual(data["usage"]["calls"], 4)
        self.assertEqual(data["usage"]["model"], r.model)
        self.assertIn("claude-opus-5-5", data["usage"]["by_model"])
        self.assertIn("crosscheck", data["engine"])
        self.assertEqual(data["engine"]["crosscheck"]["model"], "opus")
        self.assertEqual(data["engine"]["crosscheck"]["decisions"][0]["name"], "09-crosscheck-1")
        self.assertEqual({k: v for k, v in data["engine"].items() if k != "crosscheck"},
                         self.harness.report())


# --------------------------------------------------------------------------- #
# the validator
# --------------------------------------------------------------------------- #

VISITED = [OTHER, "https://second.example.com/b#top", CITED]
NOTES = (f"{OTHER}\n{SAYS}\n\nhttps://second.example.com/b\nIt is closer to 90 million km.\n\n"
         f"{CITED}\nThe tail is 100 million km long.")


def src(url, says, agrees):
    return {"url": url, "says": says, "agrees": agrees}


def result(status, *sources, correction=""):
    return {"status": status, "sources": list(sources), "correction": correction, "note": "n"}


class Validator(unittest.TestCase):
    def problems(self, answer, claim=CLAIMS[0]):
        return research.crosscheck_problems(answer, claim, VISITED, NOTES)

    def test_a_valid_corroboration_has_no_problem(self):
        self.assertEqual(self.problems(result("corroborated",
                                              src("https://independent.org/a/", SAYS, "yes"))), [])

    def test_a_url_the_pass_never_fetched_is_a_problem_naming_it(self):
        found = self.problems(result("corroborated",
                                     src("https://elsewhere.org/x", SAYS, "yes")))
        self.assertTrue(any("https://elsewhere.org/x" in p for p in found), found)

    def test_words_not_in_the_notes_are_a_problem(self):
        found = self.problems(result("corroborated", src(OTHER, "It is 100 million km.", "yes")))
        self.assertTrue(any("notes" in p for p in found), found)

    def test_another_pages_words_under_this_pages_url_are_a_problem(self):
        # "closer to 90 million km" is in the notes, but under the second page
        found = self.problems(result("corroborated",
                                     src(OTHER, "It is closer to 90 million km.", "yes")))
        self.assertTrue(any(OTHER in p and "under" in p for p in found), found)

    def test_words_found_only_before_any_fetched_url_are_a_problem(self):
        notes = "I searched for the tail length and found two pages.\n" + NOTES
        preamble = src(OTHER, "I searched for the tail length", "yes")
        found = research.crosscheck_problems(result("corroborated", preamble), CLAIMS[0],
                                             VISITED, notes)
        self.assertTrue(found)
        self.assertEqual(research.crosscheck_problems(
            result("corroborated", src(OTHER, SAYS, "yes")), CLAIMS[0], VISITED, notes), [])

    def test_a_page_the_notes_name_twice_keeps_both_parts(self):
        notes = f"{OTHER}\n{SAYS}\n\n{CITED}\nx\n\n{OTHER}/#more\nIt also gives 100 million km."
        found = research.crosscheck_problems(
            result("corroborated", src(OTHER, "It also gives 100 million km.", "yes")),
            CLAIMS[0], VISITED, notes)
        self.assertEqual(found, [])

    def test_the_cited_host_is_dropped_with_or_without_www(self):
        for url in (CITED, CITED.replace("www.", "")):
            visited = VISITED + [url]
            only = result("corroborated", src(url, "The tail is 100 million km long.", "yes"))
            found = research.crosscheck_problems(only, CLAIMS[0], visited, NOTES)
            self.assertTrue(found, url)                  # resting only on the cited host
            self.assertFalse(any(url in p for p in found), found)  # no problem by itself
            beside = result("corroborated",
                            src(url, "The tail is 100 million km long.", "no"),
                            src(OTHER, SAYS, "yes"))
            self.assertEqual(research.crosscheck_problems(beside, CLAIMS[0], visited, NOTES), [])

    def test_corroborated_with_a_counted_no_is_a_problem(self):
        found = self.problems(result("corroborated", src(OTHER, SAYS, "yes"),
                                     src("https://second.example.com/b",
                                         "It is closer to 90 million km.", "no")))
        self.assertTrue(found)

    def test_contradicted_needs_a_counted_no_and_a_correction(self):
        no = src("https://second.example.com/b", "It is closer to 90 million km.", "no")
        self.assertTrue(self.problems(result("contradicted", no)))
        self.assertEqual(self.problems(result("contradicted", no, correction="about 90 million km")),
                         [])
        self.assertTrue(self.problems(result("contradicted", src(OTHER, SAYS, "yes"),
                                             correction="x")))

    def test_disputed_needs_a_counted_yes_and_a_counted_no(self):
        yes = src(OTHER, SAYS, "yes")
        no = src("https://second.example.com/b", "It is closer to 90 million km.", "no")
        self.assertEqual(self.problems(result("disputed", yes, no)), [])
        self.assertTrue(self.problems(result("disputed", yes)))
        self.assertTrue(self.problems(result("disputed", no)))

    def test_not_found_needs_nothing(self):
        self.assertEqual(self.problems(result("not_found")), [])

    def test_a_claim_with_no_url_excludes_no_host(self):
        for none in ("no url", ""):
            claim = dict(CLAIMS[0], source_url=none)
            only = result("corroborated", src(CITED, "The tail is 100 million km long.", "yes"))
            self.assertEqual(research.crosscheck_problems(only, claim, VISITED, NOTES), [], none)


class AskedAgain(_Case):
    def test_a_bad_url_is_asked_again_with_the_problem(self):
        bad = corroborated(url="https://never-fetched.org/x")
        r = self.researcher(crosscheck=1, checks={"09-crosscheck-1": bad,
                                                  "09-crosscheck-1-again": corroborated()})
        data = r.run()
        self.assertIn("09-crosscheck-1-again", self.checker.prompts)
        self.assertIn("https://never-fetched.org/x", self.checker.prompts["09-crosscheck-1-again"])
        self.assertEqual(data["crosscheck"][0]["status"], "corroborated")

    def test_still_invalid_it_is_unverified_and_never_corroborated(self):
        bad = corroborated(url="https://never-fetched.org/x")
        r = self.researcher(crosscheck=1, checks={"09-crosscheck-1": bad,
                                                  "09-crosscheck-1-again": bad})
        data = r.run()
        entry = data["crosscheck"][0]
        self.assertEqual(entry["status"], "unverified")
        self.assertTrue(entry["problems"])
        claim = next(c for c in data["claims"] if c["claim"] == CLAIMS[0]["claim"])
        self.assertEqual(claim["independent"]["status"], "unverified")
        self.assertEqual(claim["independent"]["hosts"], [])


# --------------------------------------------------------------------------- #
# what the dossier keeps
# --------------------------------------------------------------------------- #

class Output(_Case):
    def test_the_dossier_carries_the_check_and_each_claims_independent_status(self):
        data = self.researcher().run()
        self.assertEqual([e["claim"] for e in data["crosscheck"]],
                         [CLAIMS[0]["claim"], CLAIMS[1]["claim"], CLAIMS[4]["claim"]])
        entry = data["crosscheck"][0]
        for key in ("claim", "status", "sources", "correction", "note", "visited", "problems"):
            self.assertIn(key, entry)
        self.assertEqual(entry["visited"], [OTHER])
        by = {c["claim"]: c for c in data["claims"]}
        self.assertEqual(by[CLAIMS[0]["claim"]]["independent"],
                         {"status": "corroborated", "hosts": ["independent.org"]})
        self.assertNotIn("independent", by[CLAIMS[3]["claim"]])
        # CLAIMS_SCHEMA itself is not changed
        self.assertNotIn("independent",
                         research.CLAIMS_SCHEMA["properties"]["claims"]["items"]["properties"])

    def test_the_readme_prints_an_independent_check_line(self):
        dossier = research.Dossier(self.root, self.idea, at=self.root)
        data = self.researcher().run()
        research.write_dossier(dossier, data, [])
        md = (self.root / "README.md").read_text(encoding="utf-8")
        block = md.split(CLAIMS[0]["claim"], 1)[1].split("\n- ", 1)[0]
        self.assertIn("Independent check: corroborated by independent.org", block)
        saved = json.loads((self.root / "dossier.json").read_text())
        self.assertIn("crosscheck", saved)

    def test_a_pass_whose_fetch_was_denied_is_a_provenance_warning(self):
        FakeEngine.script = staticmethod(lambda prompt: [
            search("q"), said("nothing"),
            done([{"tool_name": "WebFetch", "tool_input": {"url": OTHER}}])])
        data = self.researcher(crosscheck=1, checks={
            "09-crosscheck-1": {"status": "not_found", "sources": [], "correction": "",
                                "note": "n"}}).run()
        warned = [w for w in data["warnings"] if w.startswith(research.CROSSCHECK_WARNING)]
        self.assertEqual(len(warned), 1, data["warnings"])
        self.assertIn("WebFetch denied", warned[0])

    def test_a_pass_that_fetched_no_page_is_a_provenance_warning(self):
        self.assertEqual(research.provenance_warnings([], crosscheck=[
            {"claim": "c", "visited": [], "searches": 2, "denied": []}]),
            ["cross-check 1: opened no page, so it corroborates nothing"])


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

class Command(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"

    def yt(self, *argv):
        args = cli.build_parser().parse_args(list(argv))
        with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            code = args.run(args)
        self.err = err.getvalue()
        return code

    def test_the_flags_are_off_and_opus_by_default(self):
        ns = cli.build_parser().parse_args(["research"])
        self.assertEqual(ns.crosscheck, 0)
        self.assertEqual(ns.reviewer_model, "opus")
        help_text = research.register(argparse.ArgumentParser().add_subparsers()).format_help()
        self.assertIn("--crosscheck", help_text)
        self.assertIn("--reviewer-model", help_text)

    def test_an_own_family_reviewer_is_refused_before_any_engine(self):
        built = []
        with mock.patch.object(research, "Researcher", lambda *a, **k: built.append(k)):
            code = self.yt("research", "--project", str(self.root), "--title", "Comets",
                           "--crosscheck", "3", "--model", "sonnet",
                           "--reviewer-model", "sonnet", "--quiet")
        self.assertEqual(code, 2)
        self.assertEqual(built, [])
        self.assertIn("different model", self.err)

    def test_another_family_is_handed_to_the_researcher(self):
        seen = {}

        class Stop(research.Researcher):
            def __init__(self, *a, **k):
                seen.update(k)
                super().__init__(*a, **k)

            def run(self):
                raise ClaudeError("stopped by the test")
        with mock.patch.object(research, "Researcher", Stop):
            code = self.yt("research", "--project", str(self.root), "--title", "Comets",
                           "--crosscheck", "3", "--model", "sonnet",
                           "--reviewer-model", "opus", "--quiet")
        self.assertEqual(code, 1)
        self.assertEqual((seen["crosscheck"], seen["reviewer_model"], seen["pooled"]),
                         (3, "opus", None))

    def test_without_the_crosscheck_the_reviewer_is_not_checked(self):
        seen = {}

        class Stop(research.Researcher):
            def __init__(self, *a, **k):
                seen.update(k)
                super().__init__(*a, **k)

            def run(self):
                raise ClaudeError("stopped by the test")
        with mock.patch.object(research, "Researcher", Stop), \
                mock.patch.object(research.reviewers, "resolve",
                                  side_effect=AssertionError("resolved")):
            code = self.yt("research", "--project", str(self.root), "--title", "Comets",
                           "--model", "opus", "--quiet")
        self.assertEqual(code, 1)
        self.assertEqual(seen["crosscheck"], 0)
        self.assertIsNone(seen["pooled"])


# --------------------------------------------------------------------------- #
# what it costs, and yt run
# --------------------------------------------------------------------------- #

class Spend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "film"
        self.root.mkdir()

    def research_row(self, extras):
        return spend.stage_estimates(self.root, ["research"],
                                     extras={"research": extras})[0]

    def test_the_increment_is_n_claims_at_the_claim_price(self):
        row = self.research_row(["--download", "--crosscheck", "8"])
        usd = round(8 * spend.CROSSCHECK_CLAIM_USD, 2)
        self.assertIn(f"+ --crosscheck ${usd:.2f}", row["source"])
        self.assertAlmostEqual(row["usd"], spend.DEFAULT_USD["research"] + usd)
        self.assertEqual(spend.flag_increments(None, "research", ["--crosscheck", "3"]),
                         [("--crosscheck", round(3 * spend.CROSSCHECK_CLAIM_USD, 2))])

    def test_a_record_holding_the_check_adds_none(self):
        engine = {"model": "sonnet", "decisions": [{"name": "03-claims"}],
                  "crosscheck": {"model": "opus",
                                 "decisions": [{"name": "09-crosscheck-1"}]}}
        (self.root / "dossier.json").write_text(json.dumps(
            {"usage": {"cost_usd": 6.0, "model": "sonnet", "calls": 9}, "engine": engine}))
        self.assertEqual(spend.flag_increments(self.root, "research", ["--crosscheck", "8"]), [])
        engine.pop("crosscheck")
        (self.root / "dossier.json").write_text(json.dumps(
            {"usage": {"cost_usd": 6.0, "model": "sonnet", "calls": 9}, "engine": engine}))
        self.assertEqual(spend.flag_increments(self.root, "research", ["--crosscheck", "8"]),
                         [("--crosscheck", round(8 * spend.CROSSCHECK_CLAIM_USD, 2))])

    def test_without_the_flag_the_estimate_is_todays(self):
        row = self.research_row(["--download"])
        self.assertEqual(row["usd"], spend.DEFAULT_USD["research"])
        self.assertEqual(row["source"], "default, nothing reported")


class Run(unittest.TestCase):
    def args(self, *argv):
        return cli.build_parser().parse_args(["run", "--title", "T", *argv])

    def walk(self, *argv):
        args = self.args(*argv)
        with mock.patch.object(cli.workspace, "resolve",
                               side_effect=AssertionError("passed the check")), \
                redirect_stderr(io.StringIO()) as err:
            try:
                code = cli._run_pipeline(args)
            except AssertionError as exc:
                self.assertEqual(str(exc), "passed the check")
                code = None
        return code, err.getvalue()

    def test_run_cross_checks_eight_claims_unless_quick(self):
        extra = cli.run_extras(self.args(), redo=set())
        self.assertEqual(extra["research"], ["--download", "--crosscheck", "8"])
        quick = cli.run_extras(self.args("--quick"), redo=set())
        self.assertEqual(quick["research"], ["--download"])

    def test_the_reviewer_reaches_research_but_never_a_gateway_one(self):
        extra = cli.run_extras(self.args("--reviewer-model", "sonnet", "--review-budget-usd",
                                         "3"), redo=set())
        self.assertEqual(extra["research"],
                         ["--download", "--crosscheck", "8", "--reviewer-model", "sonnet"])
        ns = cli.build_parser().parse_args(cli.stage_argv(
            "research", ["--project", "work/x", "--model", "opus"], extra))
        self.assertEqual((ns.crosscheck, ns.reviewer_model), (8, "sonnet"))
        for pool in ("pool", "sdk:zai/glm-5.3"):
            extra = cli.run_extras(self.args("--reviewer-model", pool), redo=set())
            self.assertEqual(extra["research"], ["--download", "--crosscheck", "8"], pool)

    def test_the_authors_family_is_refused_for_research_alone(self):
        code, said = self.walk("--model", "opus", "--skip", "keyframe,vfx,script,build,styles")
        self.assertEqual(code, 2, said)
        self.assertIn("different model", said)
        code, _ = self.walk("--model", "opus", "--skip",
                            "keyframe,vfx,script,build,styles,research")
        self.assertIsNone(code)
        code, _ = self.walk("--model", "opus", "--quick", "--skip", "keyframe,vfx")
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
