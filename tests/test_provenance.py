"""Offline checks that provenance reaches the pages: wiki art carries the composer of its mapped songs, copied from
the song index by scripts/fetch_wikis.py and kept in the committed snapshot and manifest, and each slide carries the
provenance class of the copy its caption credits.

Nothing touches the network: the fetcher runs against a temporary root and a fake web that serves canned bytes.
"""

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
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
                 song("Altale", None), song("Altale", "Sakuzyo", "bwiki")]
        self.assertEqual(self.composers([candidate(["Empedrado"], "fandom"), candidate(["Empedrado"], "bwiki"),
                                         candidate(["Altale"], "fandom"), candidate(["Altale"], "bwiki")], songs),
                         ["Keikaku Tsuukou feat. Parorto", "Yuma.Mizo", "Sakuzyo", "Sakuzyo"])

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
                candidate(["Unknown"], composer="Old credit"), candidate([], composer="Old credit")]
        result = fetch.with_composers(rows, songs)
        self.assertEqual([row.get("composer") for row in result], [None, None, None])
        self.assertEqual([list(row) for row in result], [[key for key in row if key != "composer"] for row in rows])
        self.assertEqual(rows[1]["composer"], "Old credit")  # the input is left as it was


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
        offered = self.offer("Altale.png", png("red"), collection_aliases=["Etude collection"], composer="Sakuzyo")
        art = {**offered, "info": {**offered["info"], "sha1": "f" * 40}}  # a checksum caveat adds a delivery_note
        first = self.resume(art)["assets"][0]
        self.assertEqual(list(first)[-3:], ["composer", "collection_aliases", "delivery_note"])
        self.assertEqual(first["composer"], "Sakuzyo")
        calls = len(self.web.calls)
        again = self.resume(art)["assets"][0]
        self.assertEqual(len(self.web.calls), calls)  # verified from the file on disk, not downloaded again
        self.assertEqual(list(again.items()), list(first.items()))
        renamed = self.resume({**art, "composer": "削除"})["assets"][0]
        self.assertEqual(list(renamed.items()), list({**first, "composer": "削除"}.items()))
        dropped = self.resume({key: value for key, value in art.items() if key != "composer"})["assets"][0]
        self.assertEqual(dropped, {key: value for key, value in first.items() if key != "composer"})


class CommittedWikiDataTests(unittest.TestCase):
    """The committed snapshot and manifest hold what the fetcher's own join gives for the committed song index."""

    @classmethod
    def setUpClass(cls):
        cls.songs = read_json(SONG_INDEX)["songs"]
        cls.candidates = read_json(DISCOVERY)["candidates"]
        cls.assets = read_json(MANIFEST)["assets"]

    def test_the_snapshot_holds_the_join_of_the_song_index(self):
        expected = fetch.with_composers(self.candidates, self.songs)
        differ = [row["file_title"] for row, want in zip(self.candidates, expected) if list(row.items()) != list(want.items())]
        self.assertEqual(differ, [], "Patch the snapshot with fetch_wikis.with_composers()")
        self.assertGreater(sum("composer" in row for row in self.candidates), 400)

    def test_records_carry_their_candidates_composer(self):
        by_id = {fetch.candidate_id(row): row for row in self.candidates}
        for record in self.assets:
            row = by_id.get(record["id"])
            if row is None or record.get("upstream_status"):
                continue  # a carried-forward record keeps what it had
            with self.subTest(record=record["id"]):
                self.assertEqual(record.get("composer"), row.get("composer"))
                if "composer" in record:
                    tail = [key for key in record if key in ("composer", "collection_aliases", "delivery_note")]
                    self.assertEqual(list(record)[-len(tail):], tail, "composer sits where a verified re-run puts it")

    def test_altale_carries_its_composer_on_both_wikis(self):
        altale = {record["source_id"]: record.get("composer") for record in self.assets
                  if record["title"] == "Altale" and record["kind"] == "song_art"}
        self.assertEqual(altale, {"wikis:fandom": "Sakuzyo", "wikis:bwiki": "Sakuzyo"})


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


if __name__ == "__main__":
    unittest.main()
