"""Offline checks that provenance reaches the pages: wiki art carries the composer of its mapped songs, copied from
the song index by scripts/fetch_wikis.py and kept in the committed snapshot and manifest, and each slide carries the
provenance class of the copy its caption credits, which the slideshow names next to the kind in both languages. The
archive viewer states a carried-forward record's upstream status and offers the newer version of a superseded one.

Nothing touches the network: the fetcher runs against a temporary root and a fake web that serves canned bytes.
"""

import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "data/sources/wikis.json"
DISCOVERY = "data/sources/wiki-discovery.json"
SONG_INDEX = "data/sources/wiki-song-index.json"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch = load("fetch_wikis", "scripts/fetch_wikis.py")
build = load("build_catalog", "scripts/build_catalog.py")
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)


def read_json(relative, root=ROOT):
    return json.loads((root / relative).read_text(encoding="utf-8"))


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def song(title, composer=None, source="fandom"):
    row = {"title": title, "page_url": f"https://{source}.example.test/{title}", "source_id": fetch.SOURCES[source]["id"],
           "collections": []}
    return {**row, "composer": composer} if composer else row


def candidate(titles, source="bwiki", kind="song_art", **extra):
    return {"source": source, "file_title": "File:" + "+".join(titles or ["cover"]), "kind": kind,
            "song_titles": list(titles), "collections": [], "related_pages": [], **extra}


def messages(relative):
    """The en and zh-CN tables a src/i18n/ file registers."""
    return json.loads(REGISTER.search((ROOT / relative).read_text(encoding="utf-8")).group(1))


def function_body(source, name):
    """The body of the JavaScript function declared as `function name(...)` in source. Braces are counted as they
    come, which holds for the functions checked here: their strings and regular expressions keep braces balanced."""
    start = source.index("{", re.search(rf"\bfunction {name}\(", source).end())
    depth = 0
    for index in range(start, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if not depth:
            return source[start + 1:index]
    raise AssertionError(f"function {name} is not closed")


class Slides(HTMLParser):
    """The attributes of every slide <img> (class deemo-draw) in a rendered slideshow, in order."""

    def __init__(self, markup):
        super().__init__()
        self.slides = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "img" and "deemo-draw" in (attributes.get("class") or "").split():
            self.slides.append(attributes)


def entry(*records, **fields):
    """A gallery entry as build_catalog.combine() makes it: the first record's fields plus the list of all records."""
    return {**records[0], "provenance": list(records), "gallery": True, **fields}


def png(color, width=64, height=48):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class ComposerJoinTests(unittest.TestCase):
    def composers(self, candidates, songs):
        return [row.get("composer") for row in fetch.with_composers(candidates, songs)]

    def test_the_own_wiki_answers_first_and_the_other_wiki_fills_in(self):
        songs = [song("Empedrado", "Keikaku Tsuukou feat. Parorto"), song("Empedrado", "Yuma.Mizo", "bwiki"),
                 song("Altale", None), song("Altale", "Sakuzyo", "bwiki"), song("Magnolia", "M2U"),
                 song("Magnolia", None, "bwiki")]
        rows = fetch.with_composers([candidate(["Empedrado"], "fandom"), candidate(["Empedrado"], "bwiki"),
                                     candidate(["Altale"], "fandom"), candidate(["Altale"], "bwiki"),
                                     candidate(["Magnolia"], "bwiki")], songs)
        self.assertEqual([row.get("composer") for row in rows],
                         ["Keikaku Tsuukou feat. Parorto", "Yuma.Mizo", "Sakuzyo", "Sakuzyo", "M2U"])
        # A credit from the other wiki names that wiki, right after the composer.
        self.assertEqual([row.get("composer_source") for row in rows], [None, None, "wikis:bwiki", None, "wikis:fandom"])
        self.assertEqual(list(rows[2])[-2:], ["composer", "composer_source"])

    def test_the_join_is_on_the_exact_song_title(self):
        songs = [song("Witch Hunting", "Lime", "bwiki"), song("Re: the Full moon World.", "M2U", "bwiki")]
        self.assertEqual(self.composers([candidate(["Witch hunting"]), candidate(["Re：the Full moon World."])], songs),
                         [None, None])

    def test_several_songs_join_and_respellings_count_once(self):
        songs = [song("I race the dawn", "Rabpit"), song("Sunset", "Nicode"),
                 song("Matricaria -Pazs-", 'NOMA & Apo11o"1.62"program ft.Yuki Shizaki', "bwiki"),
                 song("Matricaria ~Pazs~", "NOMA & Apo11o”1.62”program ft. Yuki Shizaki", "bwiki")]
        self.assertEqual(self.composers([candidate(["I race the dawn", "Sunset"], "fandom"),
                                         candidate(["Matricaria -Pazs-", "Matricaria ~Pazs~"])], songs),
                         ["Rabpit / Nicode", 'NOMA & Apo11o"1.62"program ft.Yuki Shizaki'])

    def test_covers_and_unmatched_art_get_none_and_a_stale_composer_goes(self):
        songs = [song("AM 05:25", "Ice"), song("Daylife", "Ice")]
        rows = [candidate(["AM 05:25", "Daylife"], "fandom", kind="collection_cover"),
                candidate(["Unknown"], composer="Old credit", composer_source="wikis:fandom"), candidate([], composer="Old credit")]
        result = fetch.with_composers(rows, songs)
        self.assertEqual([row.get("composer") for row in result], [None, None, None])
        self.assertEqual([list(row) for row in result],
                         [[key for key in row if key not in ("composer", "composer_source")] for row in rows])
        self.assertEqual(rows[1]["composer"], "Old credit")  # the input is left as it was
        # A credit that moves to the own wiki's page drops the other wiki's name.
        own = fetch.with_composers([{**rows[1], "song_titles": ["Daylife"]}], [song("Daylife", "Ice", "bwiki")])
        self.assertEqual((own[0]["composer"], own[0].get("composer_source")), ("Ice", None))


class FakeWeb:
    """Stands in for requests.get: a known URL serves its bytes, any other behaves as if the network were down."""

    def __init__(self):
        self.routes, self.calls = {}, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if url not in self.routes:
            raise requests.ConnectionError(f"offline: {url}")
        response = requests.Response()
        response.status_code, response._content, response.url = 200, self.routes[url], url
        response.headers["Content-Type"] = "image/png"
        return response


class ComposerRecordTests(unittest.TestCase):
    """fetch_wikis.main() against a temporary root and the fake web."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.web = FakeWeb()
        for patcher in (patch.object(fetch, "ROOT", self.root), patch.object(fetch.requests, "get", self.web),
                        patch.object(fetch.time, "sleep", lambda seconds: None),
                        patch.object(fetch, "fetch_illustrator_index", lambda manifest: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        dump(self.root / MANIFEST, {"schema_version": 1, "fetched_at": "2026-01-01T00:00:00Z", "failures": [], "assets": [],
                                    "sources": [{**fetch.SOURCES[key], "status": "complete"} for key in ("fandom", "bwiki")]})

    def offer(self, name, payload, **extra):
        """A BWIKI song-art candidate for the file name whose download URL serves payload."""
        url = f"https://images.example.test/{hashlib.sha1(payload).hexdigest()}/{name}"
        self.web.routes[url] = payload
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
        info = {"width": width, "height": height, "size": len(payload), "mime": "image/png", "url": url,
                "sha1": hashlib.sha1(payload).hexdigest(), "descriptionurl": f"https://wiki.example.test/{name}",
                "timestamp": "2021-09-01T00:00:00Z"}
        return {"source": "bwiki", "file_title": "文件:" + name, "info": info, "kind": "song_art",
                "song_titles": [name.rsplit(".", 1)[0]], "collections": [], "related_pages": [], **extra}

    def run_main(self, *args):
        with patch.object(sys, "argv", ["fetch_wikis.py", *args]), patch("builtins.print"):
            fetch.main()

    def resume(self, *candidates):
        dump(self.root / DISCOVERY, {"schema_version": 1, "fetched_at": "2026-02-02T00:00:00Z", "candidates": list(candidates)})
        self.run_main("--resume", "--workers", "1")
        return read_json(MANIFEST, self.root)

    def test_discovery_stores_the_composer_with_the_snapshot(self):
        art = self.offer("Altale.png", png("red"))
        with patch.object(fetch, "discover_fandom", return_value=([], [song("Altale", "Sakuzyo")], {})), \
                patch.object(fetch, "discover_bwiki", return_value=([art], [song("Altale", "sakuzyo", "bwiki")], {})):
            self.run_main("--metadata-only")
        self.assertEqual(read_json(DISCOVERY, self.root)["candidates"][0]["composer"], "sakuzyo")
        self.assertEqual(self.web.calls, [])

    def test_records_keep_the_composer_in_a_stable_place(self):
        offered = self.offer("Altale.png", png("red"), collection_aliases=["Etude collection"], composer="Sakuzyo",
                             composer_source="wikis:fandom")
        art = {**offered, "info": {**offered["info"], "sha1": "f" * 40}}  # a checksum caveat adds a delivery_note
        first = self.resume(art)["assets"][0]
        self.assertEqual(list(first)[-4:], ["composer", "composer_source", "collection_aliases", "delivery_note"])
        self.assertEqual((first["composer"], first["composer_source"]), ("Sakuzyo", "wikis:fandom"))
        calls = len(self.web.calls)
        again = self.resume(art)["assets"][0]
        self.assertEqual(len(self.web.calls), calls)  # verified from the file on disk, not downloaded again
        self.assertEqual(list(again.items()), list(first.items()))
        renamed = self.resume({**art, "composer": "削除"})["assets"][0]
        self.assertEqual(list(renamed.items()), list({**first, "composer": "削除"}.items()))
        own = {key: value for key, value in art.items() if key != "composer_source"}
        self.assertEqual(self.resume(own)["assets"][0], {key: value for key, value in first.items() if key != "composer_source"})
        dropped = {key: value for key, value in art.items() if key not in ("composer", "composer_source")}
        self.assertEqual(self.resume(dropped)["assets"][0],
                         {key: value for key, value in first.items() if key not in ("composer", "composer_source")})


def instant(stamp):
    """A fetched_at timestamp as a datetime (now() leaves out the fraction when it is zero, so strings do not sort)."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def composer_drift(manifest, snapshot):
    """Ids of the manifest's records whose composer or composer_source differs from their candidate's in the snapshot,
    in manifest order.

    Only a manifest resumed from this snapshot answers to it. The source-check workflow commits a newer snapshot and
    song index on their own (fetch_wikis.py --metadata-only), and the manifest takes their composers at the --resume
    run after that merge; until then it is older than the snapshot, or names an earlier snapshot_fetched_at."""
    if instant(manifest["fetched_at"]) < instant(snapshot["fetched_at"]):
        return []
    earlier = {source["id"] for source in manifest["sources"]
               if (source.get("discovery") or {}).get("snapshot_fetched_at") not in (None, snapshot["fetched_at"])}
    by_id = {fetch.candidate_id(row): row for row in snapshot["candidates"]}
    # A carried-forward record (upstream_status) keeps what it had.
    return [record["id"] for record in manifest["assets"]
            if record["id"] in by_id and record["source_id"] not in earlier and not record.get("upstream_status")
            and any(record.get(key) != by_id[record["id"]].get(key) for key in ("composer", "composer_source"))]


class CommittedWikiDataTests(unittest.TestCase):
    """The committed snapshot and manifest hold what the fetcher's own join gives for the committed song index."""

    @classmethod
    def setUpClass(cls):
        cls.songs = read_json(SONG_INDEX)["songs"]
        cls.snapshot = read_json(DISCOVERY)
        cls.candidates = cls.snapshot["candidates"]
        cls.manifest = read_json(MANIFEST)
        cls.assets = cls.manifest["assets"]

    def test_the_snapshot_holds_the_join_of_the_song_index(self):
        expected = fetch.with_composers(self.candidates, self.songs)
        differ = [row["file_title"] for row, want in zip(self.candidates, expected) if list(row.items()) != list(want.items())]
        self.assertEqual(differ, [], "Patch the snapshot with fetch_wikis.with_composers()")
        self.assertGreater(sum("composer" in row for row in self.candidates), 400)

    def test_records_carry_their_candidates_composer(self):
        self.assertEqual(composer_drift(self.manifest, self.snapshot), [], "Run fetch_wikis.py --resume")

    def test_composers_sit_on_song_art_where_a_verified_rerun_puts_them(self):
        wikis = {row["source_id"] for row in self.songs}
        for row in self.candidates + self.assets:
            if "composer" in row or "composer_source" in row:
                with self.subTest(row=row.get("id") or row["file_title"]):
                    self.assertEqual(row["kind"], "song_art")
                    self.assertIn("composer", row)
                    if "composer_source" in row:  # the other wiki, whose song page gave the credit
                        own = row.get("source_id") or fetch.SOURCES[row["source"]]["id"]
                        self.assertLessEqual(set(row["composer_source"].split(" / ")), wikis - {own})
                    if "id" in row:
                        tail = [key for key in row if key in ("composer", "composer_source", "collection_aliases", "delivery_note")]
                        self.assertEqual(list(row)[-len(tail):], tail, "composer sits where a verified re-run puts it")

    def test_a_newer_snapshot_does_not_hold_back_the_manifest(self):
        # The source-check workflow's pull request: upstream credits BWIKI Magnolia differently, discovery rewrites the
        # song index and the snapshot, and the manifest keeps the earlier credit until the --resume after the merge.
        art = candidate(["Magnolia"], composer="M2U feat. Guriri")
        record = {"id": fetch.candidate_id(art), "source_id": "wikis:bwiki", "kind": "song_art", "composer": "M2U feat. Guriri"}
        manifest = {"fetched_at": "2026-10-08T11:50:01.560814Z", "sources": [{"id": "wikis:bwiki"}], "assets": [record]}
        resumed = {"fetched_at": "2026-10-08T11:40:06.359430Z",
                   "candidates": fetch.with_composers([art], [song("Magnolia", "M2U feat. Guriri (arr. someone)", "bwiki")])}
        self.assertEqual(composer_drift(manifest, resumed), [record["id"]])  # resumed from this snapshot: held to it
        self.assertEqual(composer_drift({**manifest, "assets": [{**record, "upstream_status": "removed"}]}, resumed), [])
        newer = {**resumed, "fetched_at": "2026-10-15T03:00:00Z"}
        self.assertEqual(composer_drift(manifest, newer), [])
        # A manifest that names the snapshot it was resumed from answers to that one only.
        named = {**manifest, "fetched_at": "2026-10-16T00:00:00Z",
                 "sources": [{"id": "wikis:bwiki", "discovery": {"snapshot_fetched_at": resumed["fetched_at"]}}]}
        self.assertEqual(composer_drift(named, newer), [])
        self.assertEqual(composer_drift(named, resumed), [record["id"]])

    def test_altale_carries_its_composer_on_both_wikis(self):
        altale = {record["source_id"]: (record.get("composer"), record.get("composer_source")) for record in self.assets
                  if record["title"] == "Altale" and record["kind"] == "song_art"}
        # Fandom's Altale page names no composer; the credit comes from BWIKI's, and the record says so.
        self.assertEqual(altale, {"wikis:fandom": ("Sakuzyo", "wikis:bwiki"), "wikis:bwiki": ("Sakuzyo", None)})


class SlideProvenanceTests(unittest.TestCase):
    WIKI = {"id": "wikis:bwiki:1", "source_id": "wikis:bwiki", "family": "wikis", "kind": "song_art",
            "provenance": "Public community wiki upload; original creator/upload lineage not independently established."}
    REPOST = {"id": "archives:tumblr:1", "source_id": "archives:tumblr", "family": "archives", "kind": "song_art",
              "provenance": "community_repost"}
    ARTIST = {"id": "artists:a:1", "source_id": "artists:a", "family": "artists", "kind": "song_art", "rights": "Artist's own."}
    LEGACY = {"id": "legacy:magnolia", "source_id": "legacy", "family": "legacy", "kind": "song_art"}

    def test_the_credited_record_names_the_class(self):
        cases = {
            "wiki upload": (entry(self.WIKI), "wiki"),
            "archives class token": (entry(self.REPOST), "community_repost"),
            "artist upload": (entry(self.ARTIST), None),
            "legacy texture": (entry(self.LEGACY), None),
            "artist file also reposted": (entry(self.ARTIST, self.REPOST), None),
            "wiki file also reposted": (entry(self.WIKI, self.REPOST), "wiki"),
            "prose instead of a token": (entry({**self.REPOST, "provenance": "Reposted somewhere."}), None),
            "a bare record": (self.REPOST, "community_repost"),
        }
        for name, (asset, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(build.slide_provenance(asset).get("data-provenance"), expected)

    def test_rendered_slides_carry_the_class_next_to_the_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "templates").mkdir()
            (root / "templates/slideshow.html").write_text("<main>\n@python-work-area\n</main>\n", encoding="utf-8")
            assets = [entry(record, url=f"assets/{index}.png", title=f"Art {index}", source_name="Source",
                            page_url="https://example.test/", width=8, height=8)
                      for index, record in enumerate((self.REPOST, self.ARTIST))]
            slides = Slides(build.render_slideshow(root, {"assets": assets})).slides
        self.assertEqual([slide.get("data-provenance") for slide in slides], ["community_repost", None])
        self.assertEqual(list(slides[0])[list(slides[0]).index("data-kind") + 1], "data-provenance")


class CommittedSlideshowTests(unittest.TestCase):
    """The generated slideshow (index.html) qualifies reposts and wiki uploads, and credits wiki art's composer."""

    @classmethod
    def setUpClass(cls):
        cls.slides = Slides((ROOT / "index.html").read_text(encoding="utf-8")).slides
        cls.messages = messages("src/i18n/slideshow.js")
        cls.catalog = read_json("data/catalog.json")

    def test_every_slide_class_has_a_label_in_both_languages(self):
        tokens = {slide["data-provenance"] for slide in self.slides if slide.get("data-provenance")}
        self.assertIn("community_repost", tokens)
        # The slideshow names every class the archive viewer labels, so a new class is labelled on both pages.
        archive = {key.split(".", 2)[2] for key in messages("src/i18n/archive.js")["en"] if key.startswith("provenance.class.")}
        for language, table in self.messages.items():
            with self.subTest(language=language):
                self.assertEqual(sorted(token for token in tokens | archive if f"slideshow.provenance.{token}" not in table), [])

    def test_reposts_and_wiki_uploads_are_qualified(self):
        by_source = {}
        for slide in self.slides:
            by_source.setdefault(slide["data-source-id"].split(":")[0], []).append(slide.get("data-provenance"))
        tumblr = [slide.get("data-provenance") for slide in self.slides if slide["data-source-id"] == "archives:rayarkmusic-tumblr"]
        self.assertTrue(tumblr, "No Tunes of Rayark slides")
        self.assertEqual(set(tumblr), {"community_repost"})
        self.assertEqual(set(by_source["wikis"]), {"wiki"})
        self.assertEqual(set(by_source["legacy"]) | set(by_source["artists"]), {None})

    def test_the_template_qualifies_the_kind(self):
        # No offline test runs the page, so the wiring is checked in the source: notesFor looks up the slide's class
        # label, and when there is one it goes into the eyebrow as a notes-provenance span inside the notes-kind label.
        notes = function_body((ROOT / "templates/slideshow.html").read_text(encoding="utf-8-sig"), "notesFor")
        self.assertRegex(notes, r'var origin = data\.provenance && DEEMO_I18N\.has\("slideshow\.provenance\." \+ data\.provenance\)'
                                r'\s*\? DEEMO_I18N\.t\("slideshow\.provenance\." \+ data\.provenance\) : "";')
        self.assertRegex(notes, r'var label = element\("span", "notes-kind", kind\);'
                                r'\s*if \(origin\) label\.append\(element\("span", "notes-provenance", [^;]*\+ origin\)\);'
                                r'\s*eyebrow\.append\(label\);')
        self.assertRegex(notes, r"notes\.append\(eyebrow, title\);")

    def test_wiki_art_is_credited_and_found_by_its_composer(self):
        records = [record for asset in self.catalog["assets"] for record in asset["provenance"]]
        manifest = {record["id"]: record.get("composer") for record in read_json(MANIFEST)["assets"]}
        wiki = {record["id"]: record.get("composer") for record in records if record["family"] == "wikis"}
        self.assertEqual(sorted(wiki), sorted(manifest))
        self.assertEqual([key for key in sorted(wiki) if wiki[key] != manifest[key]], [], "Rebuild the catalog")
        # The archive search indexes every record's composer: "Sakuzyo" finds the Fandom and BWIKI Altale too.
        found = {record["source_id"] for asset in self.catalog["assets"] if asset["gallery"]
                 if any("Sakuzyo" in (record.get("composer") or "") for record in asset["provenance"])
                 for record in asset["provenance"] if record["title"] == "Altale"}
        self.assertTrue({"legacy", "wikis:fandom", "wikis:bwiki"} <= found, found)
        credited = [slide for slide in self.slides if slide["data-source-id"].startswith("wikis:") and slide.get("data-composer")]
        self.assertGreater(len(credited), 400)
        self.assertIn("Sakuzyo", {slide["data-composer"] for slide in credited if slide["data-title"] == "Altale"})


class ArchiveUpstreamTests(unittest.TestCase):
    """The archive viewer states every upstream status and offers the newer version of a superseded record."""

    def test_every_upstream_status_the_build_accepts_is_labelled(self):
        for language, table in messages("src/i18n/archive.js").items():
            with self.subTest(language=language):
                self.assertEqual([status for status in build.UPSTREAM_STATUSES if f"upstream.{status}" not in table], [])
                self.assertIn("{status}", table["provenance.upstream"])
                self.assertEqual(set(re.findall(r"\{(\w+)\}", table["provenance.superseded_by"])), {"width", "height"})

    def test_a_superseded_record_offers_its_newer_version(self):
        # As for the slideshow, the wiring is checked in the source: the record's upstream notes end with a paragraph
        # holding a button that shows the newer entry, and show() opens that entry, clearing filters that hide it.
        script = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        self.assertRegex(function_body(script, "provenanceBlock"), r"if \(p\.upstream_status\) block\.append\(\.\.\.upstreamNotes\(p\)\);")
        notes = function_body(script, "upstreamNotes")
        self.assertRegex(notes, r"record\.id === p\.superseded_by")
        self.assertIn('t("provenance.superseded_by", {width: newer.width, height: newer.height})', notes)
        self.assertRegex(notes, r'button\.addEventListener\("click", \(\) => show\(newer\)\);')
        self.assertRegex(notes, r"paragraph\.append\(button\);\s*notes\.push\(paragraph\);")
        self.assertRegex(notes, r"return notes;\s*$")
        show = function_body(script, "show")
        self.assertRegex(show, r'if \(!filtered\.includes\(asset\)\) \{ \$\("filters"\)\.reset\(\); filter\(\); \}')
        self.assertRegex(show, r"const index = filtered\.indexOf\(asset\);\s*if \(index < 0\) return;\s*open\(index\);")
        self.assertRegex(show, r'\$\("viewer-title"\)\.focus\(\);\s*$')


if __name__ == "__main__":
    unittest.main()
