"""Offline checks that provenance reaches the pages: wiki art carries the composer of its mapped songs, copied from
the song index by scripts/fetch_wikis.py.

Nothing touches the network: the fetcher runs against a temporary root and a fake web that serves canned bytes.
"""

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
