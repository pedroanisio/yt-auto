"""retention.py: the published film's audience curve, read back onto its beats.

Every threshold in docs/narrative/ is a prior until the channel's own curves
replace it (README item 4), and nothing read them back. `yt retention` takes
what the operator reads off YouTube Studio for one published film (Intro at
0:30, top moments, spikes, dips, the standard and the typical curve, the
traffic shares; judging 1), places each timestamp on the shot build.json
had on screen, and files findings for `yt improve` in the corpus's terms: a
dip after 0:30 names its beat and whether viewers skipped or left (judging
3), a late top moment is `move_earlier`, the Intro is a hook finding only
below the channel's median and with little search or external traffic
(structure 38), a steep last 5% is an outro reading and a spike after the
payoff an unforeshadowed one (structure 40). Each carries the corpus rule it
applies and a rubric key as a labelled guess, and every constant and house
reading is tagged `sanity_bound` (judging 6). No model, no network: the
mapping is arithmetic.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytstudio import cli, doctor, harness, narrative, retention, workspace  # noqa: E402
from ytstudio.core import read_json, write_json  # noqa: E402
from ytstudio.harness import enum_violations, missing_fields, type_errors  # noqa: E402

from tests.test_doc_narrative import CORPUS, FILES, rules_of  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "retention"
EXPORT = FIXTURES / "studio-export.json"


def corpus_ids() -> set[str]:
    return {r.id for name in FILES for r in rules_of(CORPUS / name)}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def baseline(films=3, median=50.0) -> dict:
    return {"films": films, "intro_median": median}


def read(ingest=None, base=None, script=None, build=None) -> dict:
    data = ingest if ingest is not None else retention.ingest([EXPORT])
    mapped = retention.map_to_beats(data, script if script is not None else fixture("script.json"),
                                    build if build is not None else fixture("build.json"))
    return retention.reading(data, mapped, base if base is not None else baseline())


def by_tag(rows: list[dict], tag: str) -> list[dict]:
    return [r for r in rows if r["tag"] == tag]


class NoModel:
    """Any harness engine: the stage must never build one."""

    def __init__(self, config):
        raise AssertionError("yt retention asked a model")


class Ingest(unittest.TestCase):
    def test_the_fixture_export_loads_into_the_ingest_schema(self):
        data = retention.ingest([EXPORT])
        schema = retention.INGEST_SCHEMA
        self.assertEqual(missing_fields(data, schema) + type_errors(data, schema)
                         + enum_violations(data, schema), [])
        for field in ("intro_retention_30s", "above_typical_intro", "top_moments", "spikes",
                      "dips", "curve", "typical_curve", "traffic"):
            self.assertIn(field, schema["properties"], field)
        kinds = schema["properties"]["dips"]["items"]["properties"]["kind"]["enum"]
        self.assertEqual(sorted(kinds), ["abandoned", "skipped", "unknown"])
        self.assertEqual(set(schema["properties"]["traffic"]["properties"]),
                         {"search", "external", "suggested", "browse"})
        self.assertEqual([d["timestamp"] for d in data["dips"]], [20, 48, 76])

    def test_a_file_that_breaks_the_contract_is_refused_by_name(self):
        bad = fixture("studio-export.json")
        bad["dips"][1]["kind"] = "boring"
        del bad["intro_retention_30s"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            write_json(path, bad)
            with self.assertRaises(ValueError) as caught:
                retention.ingest([path])
        said = str(caught.exception)
        self.assertIn("intro_retention_30s", said)
        self.assertIn("dips[1].kind", said)

    def test_the_readings_may_be_placed_in_several_files(self):
        # the operator reads Studio's panes one at a time: the key moments in
        # one file, the curves in another, the traffic in a third
        whole = fixture("studio-export.json")
        parts = ({k: whole[k] for k in ("intro_retention_30s", "above_typical_intro",
                                        "top_moments", "spikes", "dips")},
                 {k: whole[k] for k in ("curve", "typical_curve", "duration_seconds")},
                 {"traffic": whole["traffic"]})
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n, part in enumerate(parts):
                paths.append(Path(tmp) / f"{n}.json")
                write_json(paths[-1], part)
            data = retention.ingest(paths)
        for key in ("dips", "curve", "traffic", "intro_retention_30s"):
            self.assertEqual(data[key], whole[key], key)


class Mapping(unittest.TestCase):
    def setUp(self):
        self.mapped = retention.map_to_beats(retention.ingest([EXPORT]), fixture("script.json"),
                                             fixture("build.json"))

    def moment(self, kind, t):
        return next(m for m in self.mapped["moments"]
                    if m["moment"] == kind and m["timestamp"] == t)

    def test_each_timestamp_lands_on_the_shot_build_json_had_on_screen(self):
        self.assertEqual(self.mapped["duration"], 120)
        for kind, t, shot, section in (("dip", 48, "2.1", 2), ("dip", 76, "2.3", 2),
                                       ("top_moment", 92, "3.1", 3), ("spike", 115, "3.3", 3),
                                       ("dip", 20, "1.2", 1)):
            m = self.moment(kind, t)
            self.assertEqual((m["shot"], m["section"]), (shot, section), (kind, t))
        self.assertEqual(self.moment("dip", 48)["heading"], "Light is too weak")
        self.assertEqual(self.moment("dip", 48)["kind"], "skipped")

    def test_the_question_ledger_names_what_was_open_at_the_moment(self):
        # Q2 opens on 2.1 and closes on 2.3; Q1 runs from 1.1 to 3.2
        self.assertEqual(self.moment("dip", 48)["questions"], ["Q1", "Q2"])
        self.assertEqual(self.moment("dip", 20)["questions"], ["Q1"])
        self.assertEqual(self.moment("spike", 115)["questions"], [])

    def test_a_script_without_a_ledger_still_maps(self):
        script = fixture("script.json")
        del script["questions"]
        mapped = retention.map_to_beats(retention.ingest([EXPORT]), script, fixture("build.json"))
        dip = next(m for m in mapped["moments"] if m["timestamp"] == 48)
        self.assertEqual(dip["shot"], "2.1")
        self.assertNotIn("questions", dip)

    def test_the_payoff_is_where_build_json_marks_it(self):
        self.assertEqual(self.mapped["payoff_end"], 112.0)

    def test_a_moment_past_the_last_shot_is_the_last_shot_and_said(self):
        data = retention.ingest([EXPORT])
        data["spikes"].append({"timestamp": 130})
        mapped = retention.map_to_beats(data, fixture("script.json"), fixture("build.json"))
        late = next(m for m in mapped["moments"] if m["timestamp"] == 130)
        self.assertEqual(late["shot"], "3.3")
        self.assertTrue(any("130" in n for n in mapped["notes"]), mapped["notes"])


class Findings(unittest.TestCase):
    def test_one_finding_per_dip_after_the_intro_with_its_beat_and_tag(self):
        rows = read()["findings"]
        dips = [r for r in rows if r["tag"].startswith("dip-")]
        self.assertEqual([(r["timestamp"], r["shot"], r["section"], r["tag"]) for r in dips],
                         [(48, "2.1", 2, "dip-skipped"), (76, "2.3", 2, "dip-abandoned")])
        for r in dips:
            self.assertEqual(r["rule"], "judging 3")
        # the standard curve's 40 against the typical curve's 45.2 at 0:48
        self.assertAlmostEqual(dips[0]["delta_vs_typical"], -5.2, places=3)

    def test_a_dip_of_unknown_kind_is_not_guessed(self):
        data = retention.ingest([EXPORT])
        data["dips"][1]["kind"] = "unknown"
        rows = read(data)["findings"]
        self.assertEqual([r["tag"] for r in rows if r["timestamp"] == 48], ["dip-unknown"])

    def test_a_late_top_moment_is_move_earlier_and_an_early_one_nothing(self):
        rows = by_tag(read()["findings"], "move_earlier")
        self.assertEqual([(r["timestamp"], r["shot"]) for r in rows], [(92, "3.1")])
        self.assertEqual(rows[0]["rule"], "judging 3")
        self.assertTrue(rows[0]["sanity_bound"])        # "late" is a house bound

    def test_a_spike_after_the_payoff_is_an_unforeshadowed_payoff(self):
        rows = by_tag(read()["findings"], "unforeshadowed-payoff")
        self.assertEqual([(r["timestamp"], r["shot"]) for r in rows], [(115, "3.3")])
        self.assertEqual(rows[0]["rule"], "structure 40")
        self.assertTrue(rows[0]["sanity_bound"])

    def test_no_payoff_marked_no_unforeshadowed_reading(self):
        build = fixture("build.json")
        for shot in build["shots"]:
            shot["function"] = ""
        got = read(build=build)
        self.assertEqual(by_tag(got["findings"], "unforeshadowed-payoff"), [])
        self.assertTrue(any("payoff" in n for n in got["notes"]))

    def test_a_steep_final_five_percent_is_an_outro_reading_tagged_sanity_bound(self):
        rows = by_tag(read()["findings"], "outro")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "structure 40")
        self.assertEqual(rows[0]["timestamp"], 114.0)       # 95% of 120 s
        self.assertEqual(rows[0]["shot"], "3.3")
        said = " ".join(rows[0]["sanity_bound"])
        self.assertIn("structure 40", said)
        self.assertEqual(retention.BOUNDS["outro_ratio"]["value"], 2.0)

    def test_an_even_tail_is_no_outro_reading(self):
        data = retention.ingest([EXPORT])
        data["curve"][-1]["retention"] = 30.0     # the last 5% falls as the 20% before
        self.assertEqual(by_tag(read(data)["findings"], "outro"), [])

    def test_the_intro_is_a_hook_finding_below_the_median_with_little_outside_traffic(self):
        rows = by_tag(read()["findings"], "intro")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "structure 38")
        self.assertEqual(rows[0]["hypothesis"], "hook")
        self.assertEqual(rows[0]["section"], 1)
        self.assertIsNone(rows[0]["shot"])            # the opening, not one line
        self.assertTrue(any("traffic" in s for s in rows[0]["sanity_bound"]))

    def test_no_intro_finding_above_the_median(self):
        self.assertEqual(by_tag(read(base=baseline(median=40.0))["findings"], "intro"), [])

    def test_no_intro_finding_when_search_or_external_traffic_is_high(self):
        data = retention.ingest([EXPORT])
        data["traffic"] = {"search": 70.0, "external": 10.0, "suggested": 15.0, "browse": 5.0}
        self.assertEqual(by_tag(read(data)["findings"], "intro"), [])

    def test_no_intro_finding_without_a_channel_baseline_or_traffic(self):
        got = read(base={"films": 0, "intro_median": None})
        self.assertEqual(by_tag(got["findings"], "intro"), [])
        self.assertTrue(any("baseline" in n for n in got["notes"]))
        data = retention.ingest([EXPORT])
        del data["traffic"]
        self.assertEqual(by_tag(read(data)["findings"], "intro"), [])

    def test_a_dip_read_from_the_relative_curve_is_no_timestamp_finding(self):
        # judging 2: only the standard curve's timestamps reach the script
        data = retention.ingest([EXPORT])
        data["dips"][1]["curve"] = "relative"
        got = read(data)
        self.assertEqual([r["timestamp"] for r in got["findings"] if r["tag"].startswith("dip")],
                         [76])
        self.assertTrue(any("relative" in n for n in got["notes"]))

    def test_every_finding_names_a_corpus_rule_and_a_rubric_key(self):
        ids = corpus_ids()
        rows = read()["findings"]
        self.assertEqual(sorted({r["tag"] for r in rows}),
                         ["dip-abandoned", "dip-skipped", "intro", "move_earlier", "outro",
                          "unforeshadowed-payoff"])
        for r in rows:
            self.assertIn(r["rule"], ids, r)
            self.assertIn(r["hypothesis"], narrative.KEYS, r)
            self.assertEqual((r["source"], r["owner"]), ("retention", "script"))
        for tag, row in retention.TAGS.items():
            self.assertIn(row["rule"], ids, tag)
            self.assertIn(row["hypothesis"], narrative.KEYS, tag)

    def test_the_hypothesis_is_said_as_a_guess_never_as_the_cause(self):
        for r in read()["findings"]:
            self.assertIsNone(re.search(r"\b(because|caused|due to)\b", r["problem"], re.I), r)
            self.assertIn("hypothesis", r["problem"])
            self.assertIn(r["rule"], r["problem"])

    def test_under_ten_films_every_finding_carries_sanity_bound(self):
        for r in read(base=baseline(films=3))["findings"]:
            self.assertTrue(any("3 film" in s for s in r["sanity_bound"]), r)
        rows = read(base=baseline(films=10))["findings"]
        for r in rows:
            self.assertFalse(any("film(s)" in s for s in r["sanity_bound"]), r)
        dips = [r for r in rows if r["tag"].startswith("dip-")]
        self.assertTrue(dips)
        self.assertTrue(all(r["sanity_bound"] == [] for r in dips))
        for tag in ("outro", "unforeshadowed-payoff", "move_earlier", "intro"):
            self.assertTrue(all(r["sanity_bound"] for r in by_tag(rows, tag)), tag)

    def test_every_constant_is_a_sanity_bound_citing_the_corpus(self):
        ids = corpus_ids()
        for name, bound in retention.BOUNDS.items():
            self.assertTrue(bound["sanity_bound"], name)
            self.assertTrue(bound["corpus"], name)
            for rule in bound["corpus"]:
                self.assertIn(rule, ids, name)
        # no vendor retention band anywhere: the 50% above-typical cut is
        # recorded from the export, never a gate here (judging 6)
        self.assertNotIn("above_typical", json.dumps(retention.BOUNDS))


class Baseline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def film(self, slug, intro, seconds=120, published="2026-01-01"):
        root = self.work / slug
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / "retention.json", {"ingest": {
            "intro_retention_30s": intro, "duration_seconds": seconds,
            "published": published}})
        return root

    def test_the_median_of_the_channels_other_films_of_similar_length(self):
        self.film("a", 40.0)
        self.film("b", 50.0, seconds=150)
        self.film("c", 60.0, seconds=100)
        self.film("long", 10.0, seconds=1200)          # not of similar length
        me = self.film("me", 5.0)                      # the film itself
        got = retention.channel_baseline(self.work, exclude=me, duration=120)
        self.assertEqual(got["films"], 3)
        self.assertEqual(got["intro_median"], 50.0)

    def test_only_the_last_ten_films_count(self):
        for n in range(12):
            self.film(f"f{n:02d}", 10.0 * n, published=f"2026-01-{n + 1:02d}")
        got = retention.channel_baseline(self.work, duration=120)
        self.assertEqual(got["films"], 10)
        self.assertEqual(got["intro_median"], 65.0)     # films 2..11

    def test_an_empty_channel_has_no_median(self):
        self.assertEqual(retention.channel_baseline(self.work, duration=120),
                         {"films": 0, "intro_median": None, "slugs": []})

    def test_doctor_notes_the_baseline_size_and_what_it_means(self):
        self.film("a", 40.0)
        report = doctor.Report("environment")
        doctor.retention_baseline(report, self.work)
        notes = [f.message for f in report.findings if f.area == "retention"]
        self.assertEqual(len(notes), 1)
        self.assertIn("1 published film", notes[0])
        self.assertIn("sanity_bound", notes[0])
        self.assertEqual(report.metrics["retention_films"], 1)

    def test_doctor_says_nothing_before_any_film_is_measured(self):
        report = doctor.Report("environment")
        doctor.retention_baseline(report, self.work)
        self.assertEqual([f for f in report.findings if f.area == "retention"], [])


class Stage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.root = self.work / "why-a-comets-tail-leads"
        self.root.mkdir(parents=True)
        write_json(self.root / "idea.json", {"title": "Why a comet's tail leads"})
        write_json(self.root / "script.json", fixture("script.json"))
        write_json(self.root / "build.json", fixture("build.json"))
        (self.root / "scene.xml").write_text("<scene/>", encoding="utf-8")
        self._engine = harness.ENGINE
        harness.ENGINE = NoModel

    def tearDown(self):
        harness.ENGINE = self._engine
        self._tmp.cleanup()

    def place(self, name="studio-export.json"):
        (self.root / "retention").mkdir(exist_ok=True)
        write_json(self.root / "retention" / name, fixture("studio-export.json"))

    def run_stage(self, *extra):
        args = cli.build_parser().parse_args(
            ["retention", "--project", str(self.root), "--work", str(self.work), "--quiet",
             *extra])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.run(args)
        return code, out.getvalue(), err.getvalue()

    def test_it_is_registered_beside_the_chain_and_takes_no_model(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if getattr(a, "dest", "") == "stage").choices
        self.assertIn("retention", sub)
        flags = {o for a in sub["retention"]._actions for o in a.option_strings}
        self.assertNotIn("--model", flags)
        self.assertNotIn("--fetch", flags)
        self.assertIn("--export", flags)
        self.assertNotIn("retention", cli.PIPELINE)
        self.assertIn("retention", cli.LOCAL_ONLY)

    def test_it_writes_retention_json_registered_in_the_workspace(self):
        self.place()
        code, out, _ = self.run_stage()
        self.assertEqual(code, 0)
        self.assertEqual(workspace.ARTIFACTS["retention"], "retention.json")
        record = read_json(workspace.Project(self.root).path("retention"))
        self.assertEqual(record["ingest"]["intro_retention_30s"], 45.0)
        self.assertEqual(record["sources"], ["retention/studio-export.json"])
        self.assertEqual(record["baseline"]["films"], 0)
        tags = sorted(r["tag"] for r in record["findings"])
        self.assertIn("dip-skipped", tags)
        self.assertNotIn("intro", tags)                  # no baseline yet
        self.assertTrue(all(r["sanity_bound"] for r in record["findings"]))
        self.assertEqual(record["bounds"], retention.BOUNDS)
        self.assertIn("2.1", out)

    def test_an_export_given_by_hand_is_read_instead(self):
        code, _, _ = self.run_stage("--export", str(EXPORT))
        self.assertEqual(code, 0)
        record = read_json(self.root / "retention.json")
        self.assertEqual(record["sources"], [str(EXPORT)])

    def test_no_export_is_an_error_that_says_where_to_put_one(self):
        code, _, err = self.run_stage()
        self.assertEqual(code, 1)
        self.assertIn("retention/", err)
        self.assertFalse((self.root / "retention.json").exists())

    def test_no_build_json_is_an_error_naming_yt_build(self):
        self.place()
        (self.root / "build.json").unlink()
        code, _, err = self.run_stage()
        self.assertEqual(code, 1)
        self.assertIn("yt build", err)

    def test_the_findings_are_current_only_while_the_film_is_the_one_measured(self):
        self.place()
        self.assertEqual(retention.current(self.root), [])
        self.run_stage()
        rows = retention.current(self.root)
        self.assertTrue(rows)
        self.assertTrue(all(r["source"] == "retention" for r in rows))
        # a rebuilt film is not the one the audience watched
        (self.root / "scene.xml").write_text("<scene changed='1'/>", encoding="utf-8")
        self.assertEqual(retention.current(self.root), [])


if __name__ == "__main__":
    unittest.main()
