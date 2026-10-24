"""The asset search finds every kind of media a film can use, not just stills.

A research run once came back with one usable file for fourteen shots: every
catalogue was searched for images only, papers were sources and never
assets, nothing looked for footage, sound, structures or news, the subject's
own searches were appended to every shot so one generic still filled them
all, and Wikimedia refused the client's anonymous User-Agent. These tests pin
the search that replaced it: the kinds each catalogue yields, the bytes that
decide a file's type, the diverse shortlist, references kept as references,
papers turned into their PDF, figures and footage, the cache that knows how it
was searched, and the downloaded file reaching the dossier.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_engine import Usage  # noqa: E402
from ytstudio import assets, harness, research  # noqa: E402
from ytstudio.core import Idea  # noqa: E402


class Response(io.BytesIO):
    """What urlopen hands back: headers and a body, usable as a context."""

    def __init__(self, body: bytes, headers: dict) -> None:
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(body: bytes, content_type: str, status_range: str = ""):
    headers = {"Content-Type": content_type}
    if status_range:
        headers["Content-Range"] = status_range
    return Response(body, headers)


JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
PDF = b"%PDF-1.7\n" + b"\0" * 64


class Bytes(unittest.TestCase):
    def test_a_generic_label_is_answered_by_the_first_bytes(self):
        self.assertEqual(assets.sniff(JPEG), "image/jpeg")
        self.assertEqual(assets.sniff(PDF), "application/pdf")
        self.assertEqual(assets.sniff(b"OggS\0\2"), "application/ogg")
        self.assertEqual(assets.sniff(b"\0\0\0\x18ftypmp42"), "video/mp4")
        self.assertEqual(assets.sniff(b"RIFF\0\0\0\0WAVEfmt "), "audio/wav")
        self.assertEqual(assets.sniff(b"data_1ABC\n#"), "chemical/x-mmcif")
        self.assertEqual(assets.sniff(b"HEADER    STRUCTURE"), "chemical/x-pdb")
        self.assertEqual(assets.sniff(b"<!DOCTYPE html>"), "")

    def test_every_media_type_maps_to_its_kind(self):
        self.assertEqual(assets.kind_for("application/ogg"), "audio")
        self.assertEqual(assets.kind_for("application/sla"), "3d-model")
        self.assertEqual(assets.kind_for("model/gltf-binary"), "3d-model")
        self.assertEqual(assets.kind_for("chemical/x-cif"), "structure")
        self.assertEqual(assets.kind_for("video/webm"), "video")
        for kind in ("image", "video", "audio", "document", "3d-model",
                     "structure", "article"):
            self.assertIn(kind, assets.KINDS)

    def test_ogg_is_footage_or_sound_by_its_extension(self):
        ogg = "application/ogg"
        self.assertEqual(assets.kind_for(ogg, "https://h.example/model.ogv"), "video")
        self.assertEqual(assets.kind_for(ogg, "https://h.example/word.ogg"), "audio")
        rows = [{"kind": "audio", "content_type": ogg, "url": "https://h.example/m.ogv"},
                {"kind": "audio", "content_type": "audio/mpeg", "url": "https://h.example/a.ogv"}]
        assets.settle_kinds(rows)
        self.assertEqual([r["kind"] for r in rows], ["video", "audio"])

    def test_an_stl_mesh_becomes_obj(self):
        triangle = struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0) + b"\0\0"
        binary = b"solid but binary".ljust(80, b" ") + struct.pack("<I", 1) + triangle
        ascii_ = (b"solid s\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n"
                  b"vertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid s\n")
        for data in (binary, ascii_):
            obj = assets.stl_to_obj(data)
            self.assertIn("v 1 0 0", obj)
            self.assertIn("f 1 2 3", obj)
        self.assertIsNone(assets.stl_to_obj(b"not a mesh"))


class Verify(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(assets, "_HOST_INTERVAL", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        assets._HOST_PACES.clear()

    def hit(self, kind="image", url="https://bucket.example/f"):
        return assets.AssetHit("t", url, "x", "CC BY 4.0", kind=kind)

    def test_a_bucket_file_is_judged_by_its_bytes(self):
        with mock.patch.object(assets.urllib.request, "urlopen",
                               return_value=serve(JPEG, "binary/octet-stream",
                                                  "bytes 0-2047/90000")):
            hit = assets.verify_hit(self.hit(), "ua")
        self.assertTrue(hit.verified)
        self.assertEqual((hit.kind, hit.content_type, hit.bytes),
                         ("image", "image/jpeg", 90000))

    def test_a_pdf_counts_when_the_catalogue_offered_a_paper(self):
        for kind, ok in (("document", True), ("image", False)):
            with mock.patch.object(assets.urllib.request, "urlopen",
                                   return_value=serve(PDF, "binary/octet-stream")):
                self.assertEqual(assets.verify_hit(self.hit(kind), "ua").verified, ok, kind)

    def test_a_structure_served_as_text_is_a_structure(self):
        with mock.patch.object(assets.urllib.request, "urlopen",
                               return_value=serve(b"data_1ABC\n", "text/plain")):
            hit = assets.verify_hit(self.hit("structure"), "ua")
        self.assertTrue(hit.verified)
        self.assertEqual(hit.kind, "structure")

    def test_an_article_page_only_has_to_answer(self):
        with mock.patch.object(assets.urllib.request, "urlopen",
                               return_value=serve(b"<html>", "text/html")):
            self.assertTrue(assets.verify_hit(self.hit("article"), "ua").verified)
        with mock.patch.object(assets.urllib.request, "urlopen",
                               return_value=serve(b"<html>", "text/html")):
            self.assertFalse(assets.verify_hit(self.hit("image"), "ua").verified)

    def test_a_refusal_is_waited_out_once(self):
        refusal = urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "2"}, None)
        answers = [refusal, serve(JPEG, "image/jpeg")]

        def urlopen(*a, **k):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(assets.urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch.object(assets.time, "sleep") as slept:
            hit = assets.verify_hit(self.hit(), "ua")
        self.assertTrue(hit.verified)
        slept.assert_called_once_with(2.0)

    def test_the_client_says_how_to_reach_its_maker(self):
        self.assertRegex(assets.DEFAULT_USER_AGENT, r"\+https?://")


def hit(title, kind="image", query="comet tail", licence="CC0", url=None, rank=0):
    return assets.AssetHit(title, url or f"https://h.example/{title.replace(' ', '_')}",
                           "x", licence, kind=kind, query=query, rank=rank)


class Search(unittest.TestCase):
    """find_assets with fake catalogues; nothing touches the network."""

    def run_search(self, catalogue, needs, topics=(), sources=("fake",), **kw):
        calls = []

        def fake(query, limit, user_agent, **extra):
            calls.append(query)
            return catalogue(query)

        with mock.patch.dict(assets.SOURCES, {"fake": fake}), \
                mock.patch.object(assets, "verify_hit",
                                  side_effect=lambda h, ua, **k: setattr(h, "verified", True) or h):
            out = assets.find_assets(needs, topic_searches=list(topics), sources=sources,
                                     per_need=2, shortlist=4, verbose=False, **kw)
        return out, calls

    def test_the_subject_is_searched_once_not_for_every_shot(self):
        needs = [{"shot": f"shot {n}", "searches": [f"query {n}"], "kind": "image"}
                 for n in range(3)]
        out, calls = self.run_search(lambda q: [hit(f"comet {q}", query=q)], needs,
                                     topics=["comet"])
        self.assertEqual(calls.count("comet"), 1)
        subject = [r for r in out if r["use_for"].startswith(assets.SUBJECT)]
        self.assertEqual([r["query"] for r in subject], ["comet"])
        shots = [r for r in out if not r["use_for"].startswith(assets.SUBJECT)]
        self.assertTrue(shots)
        self.assertNotIn("comet", [r["query"] for r in shots])

    def test_a_shortlist_holds_every_relevant_kind(self):
        def catalogue(query):
            stills = [hit(f"comet tail still {n}", rank=n) for n in range(6)]
            return stills + [hit("comet tail footage", "video", rank=7),
                             hit("comet tail recording", "audio", rank=8),
                             hit("comet tail nucleus structure", "structure", rank=9),
                             hit("unrelated mesh", "3d-model", rank=10)]
        out, _ = self.run_search(catalogue, [{"shot": "comet tail", "searches": ["comet tail"],
                                              "kind": "video"}])
        kinds = [r["kind"] for r in out]
        self.assertEqual(sorted(kinds), ["audio", "image", "structure", "video"])
        self.assertNotIn("3d-model", kinds)     # irrelevant kinds do not jump the queue

    def test_a_news_article_is_kept_as_a_reference(self):
        out, _ = self.run_search(
            lambda q: [hit("comet seen from the city", "article", licence=assets.NEWS_REFERENCE),
                       hit("comet poster", licence="All rights reserved")],
            [{"shot": "comet", "searches": ["comet seen"], "kind": "article"}])
        self.assertEqual([r["kind"] for r in out], ["article"])
        row = out[0]
        self.assertFalse(row["commercial_ok"])
        self.assertTrue(row["reference_only"])
        self.assertEqual(row["licence_verified"], "")

    def test_rationed_catalogues_get_the_best_queries_only(self):
        needs = [{"shot": "comet", "searches": ["comet a", "comet b", "comet c"],
                  "kind": "image"}]
        with mock.patch.dict(assets.QUERY_CAP, {"fake": 1}):
            _, calls = self.run_search(lambda q: [], needs)
        self.assertEqual(calls, ["comet a"])

    def test_sound_only_catalogues_skip_shots_that_want_none(self):
        needs = [{"shot": "comet", "searches": ["comet"], "kind": "image"},
                 {"shot": "comet hiss", "searches": ["comet hiss"], "kind": "audio"}]
        with mock.patch.dict(assets.ONLY_FOR, {"fake": ("audio", "any")}):
            _, calls = self.run_search(lambda q: [], needs)
        self.assertEqual(calls, ["comet hiss"])

    def test_failed_searches_are_reported_not_swallowed(self):
        needs = [{"shot": "comet", "searches": ["comet"], "kind": "image"}]
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            calls = []

            def fake(query, limit, user_agent, **extra):
                calls.append(query)
                return [assets.AssetHit("", "", "fake", "", note="search failed: HTTP 429")]

            with mock.patch.dict(assets.SOURCES, {"fake": fake}):
                assets.find_assets(needs, sources=("fake",), verbose=True)
        self.assertIn("fake: 1 search(es) failed", err.getvalue())

    def test_every_default_catalogue_exists(self):
        for name in assets.DEFAULT_SOURCES:
            self.assertIn(name, assets.SOURCES)
        for kind_source in ("papers", "pdb", "news", "openverse-audio", "archive"):
            self.assertIn(kind_source, assets.DEFAULT_SOURCES)


class Nasa(unittest.TestCase):
    def test_footage_resolves_to_the_file_not_its_preview(self):
        search = {"collection": {"items": [{
            "href": "https://images-assets.nasa.gov/video/A clip/collection.json",
            "data": [{"media_type": "video", "title": "A clip", "nasa_id": "A clip"}],
            "links": [{"href": "https://images-assets.nasa.gov/video/A clip/A clip~thumb.jpg"}]}]}}
        manifest = ["http://images-assets.nasa.gov/video/A clip/A clip~orig.mp4",
                    "http://images-assets.nasa.gov/video/A clip/A clip~medium.mp4",
                    "http://images-assets.nasa.gov/video/A clip/A clip~thumb.jpg"]
        seen = []

        def get_json(url, ua, timeout=30.0):
            seen.append(url)
            return search if "images-api" in url else manifest

        with mock.patch.object(assets, "_get_json", side_effect=get_json):
            hits = assets.search_nasa("clip", 3, "ua")
        self.assertEqual(hits[0].kind, "video")
        self.assertEqual(hits[0].url,
                         "https://images-assets.nasa.gov/video/A%20clip/A%20clip~medium.mp4")
        self.assertNotIn(" ", seen[1])


META = {"pmcid": "PMC100", "is_pmc_openaccess": True, "is_retracted": False,
        "license_code": "CC BY", "title": "Tails of comets",
        "citation": "J Comets. 2020;1:1.",
        "pdf_url": "s3://pmc-oa-opendata/PMC100.1/PMC100.1.pdf?md5=x",
        "xml_url": "s3://pmc-oa-opendata/PMC100.1/PMC100.1.xml?md5=x",
        "media_urls": ["s3://pmc-oa-opendata/PMC100.1/f1.gif?md5=x",
                       "s3://pmc-oa-opendata/PMC100.1/f1.jpg?md5=x",
                       "s3://pmc-oa-opendata/PMC100.1/inline-eq.gif?md5=x",
                       "s3://pmc-oa-opendata/PMC100.1/movie1.mp4?md5=x",
                       "s3://pmc-oa-opendata/PMC100.1/table.docx?md5=x"]}
XML = ('<article><fig id="f1"><label>Figure 1</label><caption><p>A comet&apos;s '
       'two tails.</p></caption><graphic xlink:href="f1"/></fig>'
       '<supplementary-material><label>Movie 1</label><caption><p>The tail '
       'forming.</p></caption><media xlink:href="movie1.mp4"/></supplementary-material>'
       '</article>')
LISTING = "<Key>metadata/PMC100.1.json</Key><Key>metadata/PMC100.2.json</Key>"


class Papers(unittest.TestCase):
    def article(self, meta=META, **kw):
        fetched = []

        def get_json(url, ua, timeout=30.0):
            fetched.append(url)
            return meta

        def get_text(url, ua, timeout=30.0):
            return LISTING if "list-type" in url else XML

        with mock.patch.object(assets, "_get_json", side_effect=get_json), \
                mock.patch.object(assets, "_get_text", side_effect=get_text):
            return assets.pmc_article("PMC100", "ua", **kw), fetched

    def test_a_paper_is_its_pdf_figures_and_footage(self):
        hits, fetched = self.article()
        self.assertTrue(fetched[0].endswith("metadata/PMC100.2.json"))   # latest version
        by_kind = {h.kind: h for h in hits}
        self.assertEqual(sorted(by_kind), ["document", "image", "video"])
        figure = by_kind["image"]
        self.assertTrue(figure.url.endswith("/PMC100.1/f1.jpg"))       # the JPEG, not the GIF
        self.assertIn("Figure 1: A comet's two tails.", figure.title)
        self.assertIn("Tails of comets", figure.paper)
        self.assertIn("Movie 1", by_kind["video"].title)
        self.assertEqual(by_kind["document"].url,
                         "https://pmc-oa-opendata.s3.amazonaws.com/PMC100.1/PMC100.1.pdf")
        self.assertEqual({h.licence for h in hits}, {"CC BY"})
        self.assertFalse(any("inline-eq" in h.url or "docx" in h.url for h in hits))

    def test_retracted_closed_or_restricted_papers_yield_nothing(self):
        for change in ({"is_retracted": True}, {"is_pmc_openaccess": False},
                       {"license_code": "CC BY-NC"}, {"license_code": "CC BY-SA"}):
            hits, _ = self.article(META | change)
            self.assertEqual(hits, [], change)
        hits, _ = self.article(META | {"license_code": "CC BY-SA"}, allow_sa=True)
        self.assertTrue(hits)

    def test_citations_name_the_articles_they_point_at(self):
        urls = ["https://pmc.ncbi.nlm.nih.gov/articles/PMC3225288/",
                "https://pubmed.ncbi.nlm.nih.gov/11340196/",
                "https://www.example.com/no-paper-here"]
        opened = []

        def get_json(url, ua, timeout=30.0):
            return {"resultList": {"result": [{"pmcid": "PMC777", "isOpenAccess": "Y"}]}}

        with mock.patch.object(assets, "_get_json", side_effect=get_json), \
                mock.patch.object(assets, "pmc_article",
                                  side_effect=lambda p, ua, **k: opened.append(p) or []):
            assets.cited_papers(urls, verbose=False)
        self.assertEqual(sorted(opened), ["PMC3225288", "PMC777"])


class FakeEngine:
    calls: list[str] = []

    def __init__(self, config):
        self.usage = Usage()

    def ask_json(self, prompt, schema, system=None):
        FakeEngine.calls.append(prompt)
        if "keep" in schema.get("properties", {}):
            return {"keep": [{"n": 1, "as": "the shot"}]}
        return {"shots": [{"shot": "a comet", "why": "w", "kind": "image",
                           "searches": ["comet"]}],
                "topic_searches": [], "working_title": "t", "runtime": "60 s",
                "sections": [], "misconceptions": [],
                "identifiable_case": {"who": "", "source": ""}, "human_stories": []}


class Dossier(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for target, name in ((research, "ClaudeEngine"), (harness, "ENGINE")):
            patcher = mock.patch.object(target, name, FakeEngine)
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeEngine.calls = []
        idea = Idea(title="Comets", genre="explainer", description="What a tail is.")
        self.dossier = research.Dossier(Path(self._tmp.name), idea, at=Path(self._tmp.name))
        self.idea = idea

    def researcher(self, **kw):
        return research.Researcher(self.idea, self.dossier, verbose=False, **kw)

    def test_a_search_made_another_way_is_searched_again(self):
        self.dossier.save("05-assets", {"assets": [{"title": "old", "url": "u0"}]})
        record = assets.AssetHit("comet", "https://h.example/c", "x", "CC0").record()
        with mock.patch.object(assets, "find_assets", return_value=[record]) as found, \
                mock.patch.object(assets, "cited_papers", return_value=[]):
            first = self.researcher().assets([], {"sources": []})
            again = self.researcher().assets([], {"sources": []})
            other = self.researcher(allow_sa=True).assets([], {"sources": []})
        self.assertEqual(found.call_count, 2)        # the old cache, then a new policy
        self.assertEqual(first["assets"][0]["title"], "comet")
        self.assertEqual(again, first)
        self.assertTrue(other["search"]["allow_sa"])

    def test_the_outline_is_rewritten_when_the_assets_change(self):
        self.dossier.save("07-outline", {"sections": ["stale"]})
        self.dossier.save("07-outline-assets", ["u0"])
        r = self.researcher()
        r.outline({}, {"assets": [{"url": "u1"}]}, {})
        self.assertEqual(self.dossier.load("07-outline-assets"), ["u1"])
        self.assertNotEqual(self.dossier.load("07-outline"), {"sections": ["stale"]})
        calls = len(FakeEngine.calls)
        self.researcher().outline({}, {"assets": [{"url": "u1"}]}, {})
        self.assertEqual(len(FakeEngine.calls), calls)   # unchanged assets replay it

    def test_a_downloaded_file_reaches_the_dossier(self):
        assets_ = [{"url": "https://h.example/a.jpg", "title": "a"},
                   {"url": "https://h.example/m.stl", "title": "m"},
                   {"url": "https://h.example/none", "title": "n"}]
        downloads = [{"url": "https://h.example/a.jpg", "status": "ok",
                      "file": "downloads/01-a.jpg"},
                     {"url": "https://h.example/m.stl", "status": "ok",
                      "file": "downloads/02-m.obj", "source_file": "downloads/02-m.stl"},
                     {"url": "https://h.example/none", "status": "skipped: licence"}]
        research.attach_downloads(assets_, downloads)
        self.assertEqual(assets_[0]["file"], "downloads/01-a.jpg")
        self.assertEqual(assets_[1]["file"], "downloads/02-m.obj")
        self.assertEqual(assets_[1]["source_file"], "downloads/02-m.stl")
        self.assertNotIn("file", assets_[2])

    def test_the_build_reads_downloaded_media_with_what_it_is_for(self):
        from ytstudio import build
        root = Path(self._tmp.name)
        (root / "downloads").mkdir()
        (root / "downloads" / "01-a.jpg").write_bytes(JPEG)
        (root / "raw" / "08-downloads.json").write_text(json.dumps([
            {"title": "a comet", "url": "https://h.example/a.jpg", "status": "ok",
             "file": "downloads/01-a.jpg", "licence": "CC0"}]))
        media = build.local_media(root, {"assets": [
            {"title": "a comet", "kind": "image", "credit_line": "c",
             "use_for": "the opening still"}]})
        self.assertEqual([(m.path, m.about) for m in media],
                         [("downloads/01-a.jpg", "the opening still")])

    def test_a_long_name_keeps_its_extension(self):
        url = "https://h.example/" + "a-very-long-descriptive-name-" * 5 + "movie.ogv"
        name = research._filename(url, 19, "application/ogg")
        self.assertTrue(name.startswith("19-"))
        self.assertTrue(name.endswith(".ogv"), name)
        self.assertLessEqual(len(name), 83)

    def test_the_vet_is_told_every_kind_and_how_to_judge_it(self):
        record = assets.AssetHit("comet", "https://h.example/c", "x", "CC0",
                                 kind="structure", note="x-ray, 2 Å").record()
        self.researcher()._vet_assets([record])
        prompt = FakeEngine.calls[-1]
        for word in ("footage", "sound", "papers", "3D structures", "news"):
            self.assertIn(word, prompt)
        self.assertIn("(x-ray, 2 Å)", prompt)
        self.assertIn("of each kind per shot", prompt)


if __name__ == "__main__":
    unittest.main()
