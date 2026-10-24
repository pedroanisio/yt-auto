"""AUTO-137: the channel record, and the repeats it measures across films.

Every check yt-auto had for "templated" work looked inside one film. A
channel can ship the same film shape every time with nothing noticing, so
`work/channel.json` keeps each film's opening device, closing device,
section shape, treatment, palette and typography, and `channel.repeats`
says, as measured lines, where a new film is the same as an earlier one.
It decides nothing: the plan review's reviewer of another family is the
one that judges a repeat, and `yt styles` only warns.

No model is called; the projects are written here.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ytstudio import channel, qa  # noqa: E402
from ytstudio.workspace import Project  # noqa: E402


def section(heading, connective, rehook="", functions=("context",)):
    return {"heading": heading, "connective": connective, "rehook": rehook,
            "shots": [{"n": i, "function": fn} for i, fn in enumerate(functions, 1)]}


#: A script.json as `yt script` writes it, cut to what the channel reads.
SCRIPT = {
    "idea": {"title": "Why the bridge fell"},
    "hook": {"trigger": "paradox", "gap": "why it fell"},
    "ending": {"payoff_line": "One bolt.", "callback": "", "next_film": "",
               "device": "callback"},
    "reveal": {"section": 4, "constraint_section": 3},
    "sections": [
        section("The fall", "open", functions=("hook", "hook", "context")),
        section("The paint", "but", rehook="Q1", functions=("setup", "rehook", "rehook")),
        section("The steel", "therefore", functions=("context",)),
        section("The bolt", "therefore", functions=("reveal", "payoff")),
    ],
}

#: A primary treatment with a parsed background and accent.
STYLES = {"styles": [{"name": "Blueprint Line",
                      "palette": "background (#14213D), accent (#F5B700), text (#F1E9D2)",
                      "typography": "Inter Display for titles, Source Sans 3 for body, "
                                    "Helvetica captions"}]}


def plan(tokens_rehooks, reveal=(5, 4), trigger="paradox", closing="callback"):
    """A plan (no shots) whose sections join with `tokens_rehooks`: each a
    connective, or `rehook` for a section that re-opens a question."""
    sections = []
    for n, word in enumerate(tokens_rehooks, 1):
        rehook = "Q1" if word == "rehook" else ""
        connective = "but" if word == "rehook" else word
        sections.append({"heading": f"s{n}", "connective": connective, "rehook": rehook})
    return {"hook": {"trigger": trigger, "gap": "g"},
            "ending": {"payoff_line": "p", "callback": "", "next_film": "",
                       "device": closing},
            "reveal": {"section": reveal[0], "constraint_section": reveal[1]},
            "sections": sections}


class Work(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.clock = 1_700_000_000

    def film(self, slug, *, script=None, styles=None, verdict=None, at=None):
        root = self.work / slug
        root.mkdir(parents=True, exist_ok=True)
        written = []
        for name, data in (("script.json", script), ("styles.json", styles),
                           ("qa.json", {"verdict": verdict} if verdict else None)):
            if data is not None:
                (root / name).write_text(json.dumps(data), encoding="utf-8")
                written.append(root / name)
        self.clock += 1000
        for path in written:
            os.utime(path, (at or self.clock, at or self.clock))
        return Project(root)

    def rows(self):
        return json.loads((self.work / channel.RECORD_FILE).read_text())["films"]


# --------------------------------------------------------------------------- #
# the fingerprint
# --------------------------------------------------------------------------- #

class Fingerprint(Work):
    def test_it_reads_the_devices_shape_treatment_palette_and_type(self):
        print_ = channel.fingerprint(self.film("bridge", script=SCRIPT, styles=STYLES))
        self.assertEqual(print_["slug"], "bridge")
        self.assertEqual(print_["title"], "Why the bridge fell")
        self.assertFalse(print_["counted"])
        self.assertEqual(print_["opening"], "paradox")
        self.assertEqual(print_["closing"], "callback")
        self.assertEqual(print_["shape"], {
            "count": 4, "tokens": ["open", "rehook", "constraint", "reveal"],
            "functions": [["hook", "context"], ["setup", "rehook"], ["context"],
                          ["reveal", "payoff"]]})
        self.assertEqual(print_["treatment"], "Blueprint Line")
        self.assertEqual(print_["palette"]["background"], "#14213D")
        self.assertEqual(print_["palette"]["accent"], "#F5B700")
        self.assertEqual(print_["typography"], ["InterDisplay", "LiberationSans", "SourceSans3"])

    def test_the_opening_is_read_through_the_one_key(self):
        # AUTO-136 landed the opening's classification as hook.trigger
        self.assertEqual(channel.OPENING_KEY, ("hook", "trigger"))
        self.assertEqual(channel.CLOSING_KEY, ("ending", "device"))
        old = {k: v for k, v in SCRIPT.items() if k not in ("hook", "ending")}
        print_ = channel.fingerprint(self.film("old", script=old))
        self.assertEqual((print_["opening"], print_["closing"]), ("", ""))

    def test_the_tokens_follow_their_precedence(self):
        # section 1 is open even when it is the constraint; the last
        # section that is also the reveal is the reveal, not the payoff
        both = plan(["open", "but", "therefore"], reveal=(3, 1))
        self.assertEqual(channel.shape(both)["tokens"], ["open", "but", "reveal"])
        self.assertIsNone(channel.shape(both)["functions"])
        later = plan(["open", "rehook", "but", "therefore"], reveal=(2, 1))
        self.assertEqual(channel.shape(later)["tokens"], ["open", "reveal", "but", "payoff"])

    def test_a_two_swatch_palette_records_no_accent_and_no_fallback(self):
        two = {"styles": [{"name": "Chalk", "palette": "#111111, #EEEEEE"}]}
        palette = channel.fingerprint(self.film("chalk", styles=two))["palette"]
        self.assertNotIn("accent", palette)
        self.assertEqual(palette["background"], "#111111")
        self.assertFalse([k for k in palette if k.startswith("--")])
        a = channel.fingerprint(self.film("a", styles=two))
        b = channel.fingerprint(self.film("b", styles=two))
        self.assertEqual(channel.repeats(a, [b], ("palette",)), [])

    def test_typography_is_resolved_to_shipped_families_longest_first(self):
        self.assertEqual(channel.typography_families("Inter Display"), ["InterDisplay"])
        self.assertEqual(channel.typography_families("Inter for body"), ["Inter"])
        self.assertEqual(channel.typography_families("Source Sans 3"), ["SourceSans3"])
        self.assertEqual(channel.typography_families("Helvetica, bold"), ["LiberationSans"])
        self.assertEqual(channel.typography_families("a brand face, JetBrains Mono"), [])

    def test_typography_is_never_a_repeat(self):
        a = channel.fingerprint(self.film("a", styles=STYLES))
        self.assertEqual(channel.repeats(a, [dict(a, slug="b")], ("typography",)), [])
        self.assertNotIn("typography", channel.DIMENSIONS)


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #

class Record(Work):
    def test_record_upserts_by_slug_and_backfills_an_unrecorded_film(self):
        self.film("older", script=SCRIPT)
        film = self.film("bridge", script=SCRIPT, styles=STYLES)
        channel.record(self.work, film)
        self.assertEqual(sorted(r["slug"] for r in self.rows()), ["bridge", "older"])
        renamed = dict(SCRIPT, idea={"title": "The bolt"})
        (film.root / "script.json").write_text(json.dumps(renamed), encoding="utf-8")
        channel.record(self.work, film)
        rows = self.rows()
        self.assertEqual(sorted(r["slug"] for r in rows), ["bridge", "older"])
        self.assertEqual(next(r for r in rows if r["slug"] == "bridge")["title"], "The bolt")
        self.assertTrue(all(r.get("at") for r in rows))

    def test_a_standalone_run_has_no_channel(self):
        film = self.film("loose", script=SCRIPT)
        self.assertIsNone(channel.record(self.work, Project(film.root, standalone=True)))
        self.assertFalse((self.work / channel.RECORD_FILE).exists())
        self.assertEqual(channel.for_project(self.work, Project(film.root, standalone=True)),
                         [])

    def test_only_a_ready_film_counts(self):
        self.assertEqual(channel.READY, qa.READY)
        self.film("styled", styles=STYLES)
        self.film("draft", script=SCRIPT, verdict=qa.NOT_READY)
        self.film("done", script=SCRIPT, verdict=qa.READY)
        film = self.film("new", script=SCRIPT)
        channel.record(self.work, film)
        counted = {r["slug"]: r["counted"] for r in self.rows()}
        self.assertEqual(counted, {"styled": False, "draft": False, "done": True,
                                   "new": False})
        self.assertEqual([r["slug"] for r in channel.earlier(self.work, "new")], ["done"])

    def test_a_film_that_became_ready_counts_without_being_recorded_again(self):
        film = self.film("done", script=SCRIPT)
        channel.record(self.work, film)
        self.assertEqual(channel.earlier(self.work, "new"), [])
        (film.root / "qa.json").write_text(json.dumps({"verdict": qa.READY}))
        self.assertEqual([r["slug"] for r in channel.earlier(self.work, "new")], ["done"])

    def test_earlier_excludes_this_film_keeps_twelve_and_puts_the_newest_first(self):
        self.assertEqual(channel.EARLIER_LIMIT, 12)
        for n in range(14):
            self.film(f"f{n:02d}", script=SCRIPT, verdict=qa.READY)
        got = [r["slug"] for r in channel.earlier(self.work, "f13")]
        self.assertEqual(got, [f"f{n:02d}" for n in range(12, 0, -1)])
        self.assertEqual(len(channel.earlier(self.work, "x", limit=3)), 3)


# --------------------------------------------------------------------------- #
# the repeats
# --------------------------------------------------------------------------- #

def counted(slug, **kw):
    return {"slug": slug, "counted": True, "opening": "", "closing": "", "shape": None,
            "treatment": "", "palette": {}, **kw}


class Repeats(unittest.TestCase):
    def test_the_same_opening_is_flagged_with_how_many_share_it(self):
        new = counted("new", opening="paradox")
        before = [counted("a", opening="paradox"), counted("b", opening="question"),
                  counted("c", opening="paradox")]
        lines = channel.repeats(new, before, ("opening",))
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("channel: ") for line in lines))
        self.assertIn("a", lines[0])
        self.assertIn("2 of the last 3 counted films", lines[0])
        self.assertEqual(channel.repeats(new, [counted("b", opening="question")],
                                         ("opening",)), [])

    def test_the_same_closing_is_flagged(self):
        lines = channel.repeats(counted("new", closing="callback"),
                                [counted("a", closing="callback")], ("closing",))
        self.assertEqual(len(lines), 1)
        self.assertIn("closing", lines[0])

    def test_an_identical_shape_is_flagged_and_two_typical_plans_are_not(self):
        one = channel.shape(plan(["open", "but", "therefore", "but", "therefore", "but"]))
        same = channel.shape(plan(["open", "but", "therefore", "but", "therefore", "but"]))
        other = channel.shape(plan(["open", "therefore", "but", "rehook", "therefore", "but"],
                                   reveal=(5, 1)))
        self.assertEqual(one["tokens"], ["open", "but", "therefore", "constraint", "reveal",
                                         "payoff"])
        self.assertEqual(other["tokens"], ["open", "therefore", "but", "rehook", "reveal",
                                           "payoff"])
        self.assertEqual(len(channel.repeats(counted("n", shape=one),
                                             [counted("a", shape=same)], ("structure",))), 1)
        self.assertEqual(channel.repeats(counted("n", shape=one),
                                         [counted("a", shape=other)], ("structure",)), [])

    def test_a_plans_shape_matches_a_drafted_films_on_what_both_evidence(self):
        drafted = channel.shape(SCRIPT)
        planned = channel.shape({k: v for k, v in SCRIPT.items()} | {
            "sections": [{k: v for k, v in s.items() if k != "shots"}
                         for s in SCRIPT["sections"]]})
        self.assertIsNone(planned["functions"])
        self.assertEqual(len(channel.repeats(counted("n", shape=planned),
                                             [counted("a", shape=drafted)],
                                             ("structure",))), 1)
        moved = dict(drafted, functions=[["hook"], ["setup"], ["context"], ["payoff"]])
        self.assertEqual(channel.repeats(counted("n", shape=moved),
                                         [counted("a", shape=drafted)], ("structure",)), [])

    def test_the_same_treatment_is_flagged_whatever_its_case_and_spacing(self):
        lines = channel.repeats(counted("n", treatment="Blueprint  line"),
                                [counted("a", treatment="blueprint-Line")], ("treatment",))
        self.assertEqual(len(lines), 1)
        self.assertEqual(channel.repeats(counted("n", treatment="Chalk"),
                                         [counted("a", treatment="Blueprint")],
                                         ("treatment",)), [])

    def test_a_close_background_and_accent_are_flagged_and_far_ones_are_not(self):
        self.assertEqual(channel.PALETTE_DISTANCE, 12.0)
        base = {"background": "#14213D", "accent": "#F5B700"}
        near = {"background": "#16233F", "accent": "#F2B405"}
        self.assertLess(channel.delta_e(base["background"], near["background"]), 12)
        lines = channel.repeats(counted("n", palette=base), [counted("a", palette=near)],
                                ("palette",))
        self.assertEqual(len(lines), 1)
        far = {"background": "#14213D", "accent": "#E5484D"}
        self.assertGreater(channel.delta_e(base["accent"], far["accent"]), 30)
        self.assertEqual(channel.repeats(counted("n", palette=base),
                                         [counted("a", palette=far)], ("palette",)), [])

    def test_a_dimension_either_side_lacks_is_not_evidenced_never_a_repeat(self):
        for dim, empty in (("opening", ""), ("closing", ""), ("structure", None),
                           ("treatment", ""), ("palette", {})):
            with self.subTest(dimension=dim):
                key = "shape" if dim == "structure" else dim
                self.assertEqual(channel.repeats(counted("n", **{key: empty}),
                                                 [counted("a", **{key: empty})], (dim,)), [])
        self.assertEqual(channel.repeats(counted("n", palette={"background": "#14213D"}),
                                         [counted("a", palette={"background": "#14213D"})],
                                         ("palette",)), [])


if __name__ == "__main__":
    unittest.main()
