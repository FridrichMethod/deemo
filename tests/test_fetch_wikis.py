"""Offline checks for scripts/fetch_wikis.py: discovery filters, manifest safety, carry-forward and retries.

Nothing touches the network: requests.get is replaced by a fake that serves canned responses, and ROOT points at a
temporary directory, so the repository's own manifests are never read or written.
"""

import _thread
import concurrent.futures
import email.utils
import hashlib
import importlib.util
import io
import itertools
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urljoin

import requests
from PIL import Image
from requests.structures import CaseInsensitiveDict

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/fetch_wikis.py"
SPEC = importlib.util.spec_from_file_location("fetch_wikis", SCRIPT)
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)
MANIFEST = "data/sources/wikis.json"
DISCOVERY = "data/sources/wiki-discovery.json"
ILLUSTRATOR_INDEX = "data/sources/wiki-illustrator-index.json"
ILLUSTRATOR_URL = "https://wikiwiki.jp/deemo/アーティスト別リスト2"
# Where each wiki serves its image files (DOWNLOAD_LOCATIONS in scripts/check_sources.py): only there do downloads go.
BWIKI_IMAGES = "https://patchwiki.biligame.com/images/deemo/"
FANDOM_IMAGES = "https://static.wikia.nocookie.net/deemo/images/"
# FetcherRun replaces it with a no-op; the illustrator index tests put it back.
FETCH_ILLUSTRATOR_INDEX = fetch.fetch_illustrator_index


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def asset_id(source, file_title):
    return "wikis:" + source + ":" + hashlib.sha256(file_title.encode("utf-8")).hexdigest()[:16]


def image_info(width, height, name="image.png"):
    return {
        "width": width,
        "height": height,
        "size": 1000,
        "mime": "image/png",
        "sha1": "0" * 40,
        "url": f"https://images.example.test/{name}",
        "descriptionurl": f"https://wiki.example.test/{name}",
        "timestamp": "2021-09-01T00:00:00Z",
    }


def allimage(name, width, height):
    """A BWIKI allimages row: underscored name, page title with spaces."""
    return {**image_info(width, height, name), "name": name, "title": "文件:" + name.replace("_", " ")}


def song(title, collections=(), source="fandom"):
    return {
        "title": title,
        "page_url": f"https://{source}.example.test/{title}",
        "source_id": fetch.SOURCES[source]["id"],
        "collections": list(collections),
    }


def discover_bwiki(inventory, fandom_songs=(), bwiki_pages=()):
    """Run BWIKI discovery against a canned allimages inventory and canned song pages."""
    with (
        patch.object(fetch, "category", return_value=[]),
        patch.object(fetch, "pages", return_value=list(bwiki_pages)),
        patch.object(fetch, "allimages", return_value=list(inventory)),
    ):
        return fetch.discover_bwiki(list(fandom_songs))


def fandom_page(title, wikitext, *images):
    return {
        "title": title,
        "pageid": len(title),
        "fullurl": f"https://fandom.example.test/{title}",
        "categories": [],
        "revisions": [{"slots": {"main": {"*": wikitext}}}],
        "images": [{"title": image} for image in images],
    }


def bwiki_song_page(title, collection):
    """A BWIKI song page whose 所属曲包 (collection) parameter is collection."""
    return {
        **fandom_page(title, "{{歌曲|所属曲包=" + collection + "}}"),
        "fullurl": f"https://bwiki.example.test/{title}",
    }


def discover_fandom(song_pages, infos):
    """Run Fandom discovery against canned song pages (no collection pages) and canned imageinfo."""
    titles = [page["title"] for page in song_pages]
    members = [{"title": title, "ns": 0} for title in titles]
    with (
        patch.object(fetch, "category", lambda source, title: members if title == "Category:Songs" else []),
        patch.object(fetch, "pages", lambda source, requested: song_pages if requested == titles else []),
        patch.object(fetch, "imageinfo", lambda source, requested: infos),
    ):
        return fetch.discover_fandom()


def png(color, width=64, height=48):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, url, status=200, content=b"", headers=None):
        self.url, self.status_code, self.content = url, status, content
        self.headers = CaseInsensitiveDict(headers or {})
        self.consumed = 0  # body bytes handed out by iter_content()

    def json(self):
        return json.loads(self.content)

    @property
    def text(self):
        return self.content.decode("utf-8")

    @property
    def is_redirect(self):
        return "location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            chunk = self.content[start : start + chunk_size]
            self.consumed += len(chunk)
            yield chunk

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: {self.url}", response=self)


class FakeWeb:
    """Stands in for requests.get. A route is bytes (200), a (status, body, headers) tuple, or a list of these served
    in turn (the last one repeats); any other URL behaves as if the network were down. Like requests, it follows a
    redirect (a 3xx with a Location) unless allow_redirects=False. Every request is recorded in calls, its
    allow_redirects and stream options in options, and every response in responses."""

    def __init__(self):
        self.routes, self.calls, self.options, self.responses = {}, [], [], []

    def __call__(self, url, params=None, headers=None, timeout=None, allow_redirects=True, stream=False):
        for _ in range(30):  # requests' default max_redirects
            self.calls.append(url)
            self.options.append({"allow_redirects": allow_redirects, "stream": stream})
            route = self.routes.get(url)
            if isinstance(route, list):
                route = route.pop(0) if len(route) > 1 else route[0]
            if route is None:
                raise requests.ConnectionError(f"offline: {url}")
            status, body, served = route if isinstance(route, tuple) else (200, route, {"Content-Type": "image/png"})
            response = FakeResponse(url, status, body, served)
            self.responses.append(response)
            if not (allow_redirects and response.is_redirect):
                return response
            url = urljoin(url, response.headers["Location"])
        raise requests.TooManyRedirects(f"Exceeded 30 redirects: {url}")


class FetcherRun(unittest.TestCase):
    """Runs fetch_wikis.main() against a temporary repository root and the fake web."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.web, self.sleeps = FakeWeb(), []
        for patcher in (
            patch.object(fetch, "ROOT", self.root),
            patch.object(fetch.requests, "get", self.web),
            patch.object(fetch.time, "sleep", self.sleeps.append),
            patch.object(fetch, "fetch_illustrator_index", lambda manifest: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        dump(
            self.root / MANIFEST,
            {
                "schema_version": 1,
                "fetched_at": "2026-01-01T00:00:00Z",
                "failures": [],
                "assets": [],
                "sources": [{**fetch.SOURCES[key], "status": "complete"} for key in ("fandom", "bwiki")],
            },
        )

    def offer(self, name, payload, **extra):
        """A BWIKI candidate for the file name whose download URL, under BWIKI's image path, serves payload."""
        url = f"{BWIKI_IMAGES}{hashlib.sha1(payload).hexdigest()}/{quote(name)}"
        self.web.routes[url] = payload
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
        info = {
            **image_info(width, height, name),
            "url": url,
            "size": len(payload),
            "sha1": hashlib.sha1(payload).hexdigest(),
        }
        return {
            "source": "bwiki",
            "file_title": "文件:" + name,
            "info": info,
            "kind": "song_art",
            "song_titles": [name.rsplit(".", 1)[0]],
            "collections": [],
            "related_pages": [],
            **extra,
        }

    def read(self, relative):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def run_main(self, *args):
        with patch.object(sys, "argv", ["fetch_wikis.py", *args]), patch("builtins.print"):
            fetch.main()

    def resume(self, *candidates, workers=1):
        dump(
            self.root / DISCOVERY,
            {"schema_version": 1, "fetched_at": "2026-02-02T00:00:00Z", "candidates": list(candidates)},
        )
        self.run_main("--resume", "--workers", str(workers))
        return self.read(MANIFEST)


class CarryForwardTests(FetcherRun):
    def by_title(self, manifest):
        return {record["title"]: record for record in manifest["assets"]}

    def test_removed_candidate_keeps_its_record_and_file(self):
        kept, gone = self.offer("Kept.png", png("red")), self.offer("Gone.png", png("blue"))
        before = self.by_title(self.resume(kept, gone))["Gone"]
        manifest = self.resume(kept)
        records = self.by_title(manifest)
        self.assertEqual(records["Gone"], {**before, "upstream_status": "removed"})
        self.assertNotIn("upstream_status", records["Kept"])
        self.assertTrue((self.root / before["path"]).is_file())
        self.assertEqual(manifest["failures"], [])
        # When the candidate comes back, so does a plain record.
        self.assertEqual(self.by_title(self.resume(kept, gone))["Gone"], before)

    def test_reupload_keeps_the_previous_version_as_superseded(self):
        old = self.offer("Art.png", png("red"))
        first = self.resume(old)["assets"][0]
        new = self.offer("Art.png", png("green"))
        manifest = self.resume(new)
        records = {record["id"]: record for record in manifest["assets"]}
        previous_id = first["id"] + ":" + first["sha256"][:12]
        self.assertEqual(sorted(records), [first["id"], previous_id])
        self.assertEqual(records[first["id"]]["wiki_sha1"], new["info"]["sha1"])
        self.assertNotIn("upstream_status", records[first["id"]])
        self.assertEqual(
            records[previous_id],
            {**first, "id": previous_id, "upstream_status": "superseded", "superseded_by": first["id"]},
        )
        self.assertTrue(all((self.root / record["path"]).is_file() for record in records.values()))
        self.assertNotEqual(records[first["id"]]["path"], first["path"])
        # Resuming the same snapshot again changes nothing.
        self.assertEqual(self.resume(new)["assets"], manifest["assets"])

    def test_revert_to_an_earlier_upload_keeps_one_record_of_its_file(self):
        first = self.resume(self.offer("Art.png", png("red")))["assets"][0]
        green = self.resume(self.offer("Art.png", png("green")))["assets"][0]
        # Upstream reverts to the first upload: the canonical record holds its file again, and only the green version
        # stays superseded; the red one is not also kept as "superseded by" a record of the same file.
        manifest = self.resume(self.offer("Art.png", png("red")))
        records = {record["id"]: record for record in manifest["assets"]}
        green_id = first["id"] + ":" + green["sha256"][:12]
        self.assertEqual(sorted(records), [first["id"], green_id])
        self.assertEqual(
            (records[first["id"]]["path"], records[first["id"]]["sha256"]), (first["path"], first["sha256"])
        )
        self.assertNotIn("upstream_status", records[first["id"]])
        self.assertEqual(
            {**records[green_id], "fetched_at": None},
            {
                **green,
                "fetched_at": None,
                "id": green_id,
                "upstream_status": "superseded",
                "superseded_by": first["id"],
            },
        )
        files = {path.relative_to(self.root).as_posix() for path in (self.root / "assets").rglob("*") if path.is_file()}
        self.assertEqual(sorted(record["path"] for record in records.values()), sorted(files))
        # Resuming the reverted snapshot again changes nothing.
        self.assertEqual(self.resume(self.offer("Art.png", png("red")))["assets"], manifest["assets"])

    def test_verified_rerun_takes_the_mapping_fields_of_the_snapshot(self):
        # A wiki edit can change the illustrator credit, the variant note, the mapping or the file metadata without
        # touching the file. A verified re-run follows the snapshot in every direction, with the key order a fresh
        # download of the same candidate has.
        payload = png("red")
        steps = [
            {"artist": "A", "variant_note": "v", "extmetadata": {"ObjectName": {"value": "Old"}}},
            {"artist": "B", "extmetadata": {"ObjectName": {"value": "New"}}},
            {"mapping_method": "Exact normalized song title / image filename match"},
            {},
            {"artist": "A", "variant_note": "v", "extmetadata": {"ObjectName": {"value": "Old"}}},
        ]
        for index, fields in enumerate(steps):
            with self.subTest(step=index):
                extra = {key: value for key, value in fields.items() if key != "extmetadata"}
                offered = self.offer("Art.png", payload, **extra)
                if "extmetadata" in fields:
                    offered["info"]["extmetadata"] = fields["extmetadata"]
                calls = len(self.web.calls)
                record = self.resume(offered)["assets"][0]
                if index:
                    self.assertEqual(len(self.web.calls), calls)  # verified from the file on disk
                for field in ("artist", "variant_note", "mapping_method"):
                    self.assertEqual(record.get(field), offered.get(field), field)
                self.assertEqual(record.get("wiki_extmetadata"), fields.get("extmetadata"))
                fresh = fetch.download(offered, {})
                self.assertEqual(list(record), list(fresh))
                self.assertEqual({**record, "fetched_at": None}, {**fresh, "fetched_at": None})

    def test_revert_does_not_rewrite_the_file_of_another_record(self):
        # The superseded record of the first upload still points at its file, which holds the reverted bytes already:
        # rewriting it would truncate another record's file if the run died mid-write.
        first = self.resume(self.offer("Art.png", png("red")))["assets"][0]
        self.resume(self.offer("Art.png", png("green")))
        written, write_bytes, replace = [], Path.write_bytes, os.replace

        def spy_write_bytes(path, data):
            written.append(Path(path))
            return write_bytes(path, data)

        def spy_replace(source, target):
            written.append(Path(target))
            return replace(source, target)

        with patch.object(Path, "write_bytes", spy_write_bytes), patch.object(fetch.os, "replace", spy_replace):
            manifest = self.resume(self.offer("Art.png", png("red")))
        self.assertIn(first["path"], [record["path"] for record in manifest["assets"]])
        self.assertNotIn(self.root / first["path"], written)
        self.assertEqual(hashlib.sha256((self.root / first["path"]).read_bytes()).hexdigest(), first["sha256"])

    def test_an_interrupted_image_write_leaves_no_partial_file(self):
        candidate = self.offer("Art.png", png("red"))
        with patch.object(fetch.os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            fetch.download(candidate, {})
        self.assertEqual([path for path in (self.root / "assets").rglob("*") if path.is_file()], [])
        # A target that already holds other bytes (a damaged copy being replaced) keeps them, untruncated.
        record = fetch.download(candidate, {})
        target = self.root / record["path"]
        target.write_bytes(b"damaged copy")
        with patch.object(fetch.os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            fetch.download(candidate, {})
        self.assertEqual(target.read_bytes(), b"damaged copy")
        self.assertEqual([path.name for path in target.parent.iterdir()], [target.name])

    def test_failed_redownload_keeps_the_verified_record(self):
        old = self.offer("Art.png", png("red"))
        first = self.resume(old)["assets"][0]
        new = self.offer("Art.png", png("green"))
        routes = dict(self.web.routes)
        self.web.routes.clear()  # the CDN is unreachable
        manifest = self.resume(new)
        self.assertEqual(manifest["assets"], [{**first, "upstream_status": "fetch_failed"}])
        self.assertEqual([failure["title"] for failure in manifest["failures"]], ["文件:Art.png"])
        self.assertEqual({source["id"]: source["status"] for source in manifest["sources"]}["wikis:bwiki"], "partial")
        # A later successful retry supersedes the old version instead of dropping it.
        self.web.routes.update(routes)
        records = {record["id"]: record for record in self.resume(new)["assets"]}
        self.assertEqual(records[first["id"] + ":" + first["sha256"][:12]]["upstream_status"], "superseded")
        self.assertEqual(records[first["id"]]["wiki_sha1"], new["info"]["sha1"])


class CheckpointTests(FetcherRun):
    def test_interrupted_run_leaves_every_previous_record(self):
        candidates = [self.offer(f"Art {index:02}.png", png((index * 4, 0, 0))) for index in range(60)]
        self.assertEqual(len(self.resume(*candidates)["assets"]), 60)
        original, calls = fetch.download, []

        def interrupted(candidate, existing):
            calls.append(candidate)
            if len(calls) == 40:
                raise KeyboardInterrupt  # Ctrl-C after the checkpoint written at 25 results
            return original(candidate, existing)

        with patch.object(fetch, "download", interrupted), self.assertRaises(KeyboardInterrupt):
            self.resume(*candidates)
        self.assertEqual(len(self.read(MANIFEST)["assets"]), 60)

    def test_ctrl_c_cancels_the_queued_downloads_and_keeps_the_finished_ones(self):
        # Ctrl-C reaches the main thread while a worker downloads. The queued downloads are cancelled instead of all
        # running to the end, and every download that finished has its record: none is left as a file without one.
        # A shell starts a background job with SIGINT ignored, and interrupt_main() then does nothing at all. Ctrl-C at
        # a terminal reaches Python's own handler, so the test installs it, whoever started the run.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        candidates = [self.offer(f"Art {index:02}.png", png((index * 4, 0, 0))) for index in range(60)]
        web, saved, write_json = self.web, threading.Event(), fetch.write_json
        requests_made, lock = itertools.count(1), threading.Lock()

        def saving(relative, data):
            write_json(relative, data)
            saved.set()

        def interrupting(url, **options):
            with lock:  # each request takes its own number, whichever thread makes it, so exactly one is the fifth
                number = next(requests_made)
            if number == 6:
                saved.wait(10)  # the request after Ctrl-C waits until the run has saved the manifest
            response = web(url, **options)
            if number == 5:
                _thread.interrupt_main()
            return response

        with (
            patch.object(fetch.requests, "get", interrupting),
            patch.object(fetch, "write_json", saving),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.resume(*candidates)
        # The interrupted download and at most the one the worker had started; the queued ones were cancelled.
        self.assertLessEqual(len(self.web.calls), 5 + 1)
        files = {path.relative_to(self.root).as_posix() for path in (self.root / "assets").rglob("*") if path.is_file()}
        self.assertGreaterEqual(len(files), 5)
        self.assertEqual({record["path"] for record in self.read(MANIFEST)["assets"]}, files)

    def test_manifest_write_is_atomic(self):
        fetch.write_json(MANIFEST, {"assets": ["kept"]})
        with patch.object(fetch.os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            fetch.write_json(MANIFEST, {"assets": []})
        self.assertEqual(self.read(MANIFEST), {"assets": ["kept"]})
        self.assertEqual([path.name for path in (self.root / MANIFEST).parent.iterdir()], ["wikis.json"])


class DiscoveryRun(FetcherRun):
    FANDOM_STATS = {"song_pages": 1, "collection_pages": 0, "excluded": []}
    BWIKI_STATS = {"song_pages": 0, "allimages_count": 1, "excluded_large_images": []}

    def discover(self, *args):
        """Run main() with canned discovery results; returns the mock standing in for fetch_song_keys."""
        candidate = self.offer("Art.png", png("red"))
        with (
            patch.object(fetch, "discover_fandom", return_value=([], [song("Art")], self.FANDOM_STATS)),
            patch.object(fetch, "discover_bwiki", return_value=([candidate], [], self.BWIKI_STATS)),
            patch.object(fetch, "fetch_song_keys") as song_keys,
        ):
            self.run_main(*args)
        return song_keys


class ModeTests(DiscoveryRun):
    def test_metadata_only_leaves_song_keys_and_images_alone(self):
        manifest = (self.root / MANIFEST).read_bytes()
        self.discover("--metadata-only").assert_not_called()
        self.assertEqual(self.web.calls, [])
        self.assertEqual((self.root / MANIFEST).read_bytes(), manifest)
        self.assertEqual(
            sorted(path.name for path in (self.root / "data/sources").iterdir()),
            ["wiki-discovery.json", "wiki-song-index.json", "wikis.json"],
        )
        self.assertEqual([row["file_title"] for row in self.read(DISCOVERY)["candidates"]], ["文件:Art.png"])
        self.assertFalse((self.root / "assets").exists())

    def test_full_discovery_still_refreshes_song_keys(self):
        self.discover().assert_called_once_with()
        self.assertEqual([record["title"] for record in self.read(MANIFEST)["assets"]], ["Art"])


class IllustratorIndexTests(DiscoveryRun):
    """The Japanese wiki's illustrator directory, saved as reference data next to the wiki manifest."""

    ENTRY = {
        "id": "wikis:wikiwiki-illustrators",
        "name": "Japanese DEEMO Wiki illustrator directory",
        "url": ILLUSTRATOR_URL,
        "status": "complete",
        "notes": "Reference directory for illustrator provenance; availability is checked without authentication or "
        "challenge bypass.",
        "index_path": ILLUSTRATOR_INDEX,
    }
    INDEX = {
        "schema_version": 1,
        "fetched_at": "2026-10-08T00:00:00Z",
        "source_url": ILLUSTRATOR_URL,
        "rows": [{"type": "h2", "text": "illustrator", "links": []}],
    }

    def setUp(self):
        super().setUp()
        manifest = self.read(MANIFEST)
        dump(self.root / MANIFEST, {**manifest, "sources": [*manifest["sources"], self.ENTRY]})
        dump(self.root / ILLUSTRATOR_INDEX, self.INDEX)
        patcher = patch.object(fetch, "fetch_illustrator_index", FETCH_ILLUSTRATOR_INDEX)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entry(self):
        (entry,) = [source for source in self.read(MANIFEST)["sources"] if source["id"] == self.ENTRY["id"]]
        return entry

    def test_resume_leaves_the_reference_index_alone(self):
        # --resume works from the saved snapshot; like the song keys, the unrelated reference index is not refetched.
        self.web.routes[ILLUSTRATOR_URL] = (403, b"Forbidden", {})
        manifest = self.resume(self.offer("Art.png", png("red")))
        self.assertNotIn(ILLUSTRATOR_URL, self.web.calls)
        self.assertEqual(self.entry(), self.ENTRY)
        self.assertEqual(self.read(ILLUSTRATOR_INDEX), self.INDEX)
        self.assertEqual(manifest["failures"], [])

    def test_a_failed_or_empty_refresh_keeps_the_saved_index(self):
        # A Cloudflare 403, or a 200 challenge page without the directory, must not replace a good index.
        challenge = b"<html><body><p>Checking your browser...</p></body></html>"
        for status, body in ((403, b"Forbidden"), (200, challenge)):
            with self.subTest(status=status):
                self.web.routes[ILLUSTRATOR_URL] = (status, body, {"Content-Type": "text/html"})
                self.discover()
                self.assertEqual(self.entry(), self.ENTRY)
                self.assertEqual(self.read(ILLUSTRATOR_INDEX), self.INDEX)
                failures = self.read(MANIFEST)["failures"]
                self.assertEqual(
                    [(row["source_id"], row["stage"]) for row in failures], [(self.ENTRY["id"], "reference_index")]
                )

    def test_full_run_refreshes_the_index(self):
        page = '<div id="body"><h2>illustrator</h2><table><tr><td><a href="/deemo/X">X</a></td></tr></table></div>'
        self.web.routes[ILLUSTRATOR_URL] = (200, page.encode("utf-8"), {"Content-Type": "text/html"})
        self.discover()
        self.assertEqual(
            self.read(ILLUSTRATOR_INDEX)["rows"],
            [
                {"type": "h2", "text": "illustrator", "links": []},
                {"type": "tr", "text": "X", "links": [{"text": "X", "url": "/deemo/X"}]},
            ],
        )
        self.assertEqual(self.entry(), self.ENTRY)
        self.assertEqual([source["id"] for source in self.read(MANIFEST)["sources"]][-1], self.ENTRY["id"])
        self.assertEqual(self.read(MANIFEST)["failures"], [])


class DiscoveryStatsTests(DiscoveryRun):
    def test_discovery_snapshot_records_its_statistics(self):
        self.discover("--metadata-only")
        self.assertEqual(self.read(DISCOVERY)["stats"], {"fandom": self.FANDOM_STATS, "bwiki": self.BWIKI_STATS})

    def test_resume_copies_the_snapshot_statistics_into_the_manifest(self):
        dump(
            self.root / DISCOVERY,
            {
                "schema_version": 1,
                "fetched_at": "2026-03-03T00:00:00Z",
                "candidates": [],
                "stats": {"fandom": self.FANDOM_STATS, "bwiki": self.BWIKI_STATS},
            },
        )
        self.run_main("--resume")
        discovery = {source["id"]: source.get("discovery") for source in self.read(MANIFEST)["sources"]}
        self.assertEqual(discovery["wikis:bwiki"], {**self.BWIKI_STATS, "snapshot_fetched_at": "2026-03-03T00:00:00Z"})
        self.assertEqual(
            discovery["wikis:fandom"], {**self.FANDOM_STATS, "snapshot_fetched_at": "2026-03-03T00:00:00Z"}
        )

    def test_resume_of_a_snapshot_without_statistics_keeps_the_previous_ones(self):
        manifest = self.read(MANIFEST)
        manifest["sources"][1]["discovery"] = {"song_pages": 182, "allimages_count": 480, "excluded_large_images": []}
        dump(self.root / MANIFEST, manifest)
        self.resume()
        self.assertEqual(self.read(MANIFEST)["sources"][1]["discovery"], manifest["sources"][1]["discovery"])


class RetryTests(FetcherRun):
    URL = "https://wiki.biligame.com/deemo/api.php"

    def test_bwiki_edgeone_567_is_retried(self):
        self.web.routes[self.URL] = [(567, b"", {}), (200, b"{}", {})]
        self.assertEqual(fetch.get(self.URL).content, b"{}")
        self.assertEqual((len(self.web.calls), self.sleeps), (2, [1]))

    def test_retry_after_is_honoured_up_to_a_cap(self):
        self.web.routes[self.URL] = [
            (429, b"", {"Retry-After": "7"}),
            (429, b"", {"Retry-After": "86400"}),
            (200, b"", {}),
        ]
        fetch.get(self.URL)
        self.assertEqual(self.sleeps, [7, fetch.MAX_RETRY_AFTER])

    def test_retry_after_may_be_an_http_date(self):
        when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
        self.web.routes[self.URL] = [(503, b"", {"Retry-After": when}), (200, b"", {})]
        fetch.get(self.URL)
        self.assertTrue(5 <= self.sleeps[0] <= 20, self.sleeps)

    def test_attempts_are_bounded_and_client_errors_fail_at_once(self):
        self.web.routes[self.URL] = (567, b"", {})
        with self.assertRaises(requests.HTTPError):
            fetch.get(self.URL)
        self.assertEqual((len(self.web.calls), len(self.sleeps)), (3, 2))
        self.web.calls.clear()
        self.sleeps.clear()
        self.web.routes[self.URL] = (404, b"", {})
        with self.assertRaises(requests.HTTPError):
            fetch.get(self.URL)
        self.assertEqual((len(self.web.calls), self.sleeps), (1, []))


class DownloadIntegrityTests(FetcherRun):
    """The checks download() makes before it archives bytes, and --resume makes before it reuses a local file."""

    def files(self):
        return sorted(
            path.relative_to(self.root).as_posix() for path in self.root.rglob("assets/**/*") if path.is_file()
        )

    def test_a_smaller_rendition_than_the_wiki_publishes_is_refused(self):
        # A CDN that serves a downscaled rendition (a thumbnail, a negotiated WebP) must not be archived as the file:
        # the record would claim a full-size file that matches the published dimensions.
        offered = self.offer("Art.png", png("red", 32, 24))
        smaller = {**offered, "info": {**offered["info"], "width": 64, "height": 48}}
        with self.assertRaisesRegex(ValueError, r"Unexpected dimensions: \(32, 24\) != \(64, 48\)"):
            fetch.download(smaller, {})
        self.assertEqual(self.files(), [])
        # In a run, the title is a failure and the verified record of the previous upload is carried forward.
        first = self.resume(self.offer("Art.png", png("red")))["assets"][0]
        reupload = self.offer("Art.png", png("green", 32, 24))
        manifest = self.resume({**reupload, "info": {**reupload["info"], "width": 64, "height": 48}})
        self.assertEqual(manifest["assets"], [{**first, "upstream_status": "fetch_failed"}])
        self.assertEqual([failure["title"] for failure in manifest["failures"]], ["文件:Art.png"])
        self.assertIn("Unexpected dimensions", manifest["failures"][0]["error"])
        self.assertEqual(self.files(), [first["path"]])

    def test_resume_downloads_a_damaged_local_file_again(self):
        candidate = self.offer("Art.png", png("red"))
        first = self.resume(candidate)["assets"][0]
        path = self.root / first["path"]
        damaged = bytearray(path.read_bytes())
        damaged[len(damaged) // 2] ^= 1
        path.write_bytes(bytes(damaged))
        calls = len(self.web.calls)
        again = self.resume(candidate)["assets"][0]
        self.assertGreater(len(self.web.calls), calls)  # the SHA-256 of the local file no longer matches
        self.assertEqual(again["sha256"], first["sha256"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), again["sha256"])

    def test_a_payload_with_a_broken_checksum_is_refused(self):
        # A PNG whose IDAT checksum is wrong opens, and reports its size, but fails Image.verify().
        raw = bytearray(png("red"))
        start = raw.index(b"IDAT") - 4
        (length,) = struct.unpack(">I", raw[start : start + 4])
        raw[start + 8 + length] ^= 0xFF
        manifest = self.resume(self.offer("Art.png", bytes(raw)))
        self.assertEqual(manifest["assets"], [])
        self.assertEqual([failure["title"] for failure in manifest["failures"]], ["文件:Art.png"])
        self.assertIn("broken PNG file", manifest["failures"][0]["error"])
        self.assertEqual(self.files(), [])


class DownloadGuardTests(FetcherRun):
    """An image download goes only over https to the wiki's own image path (DOWNLOAD_LOCATIONS in
    scripts/check_sources.py), for the first URL and every redirect, and reads at most MAX_IMAGE_BYTES. The snapshot it
    downloads from comes from a job that runs third-party code, so nothing else in it is trusted."""

    def files(self):
        return sorted(
            path.relative_to(self.root).as_posix() for path in self.root.rglob("assets/**/*") if path.is_file()
        )

    def serve(self, routes):
        """Replace the fake web's routes and forget the requests made so far."""
        self.web.routes = routes
        for log in (self.web.calls, self.web.options, self.web.responses):
            log.clear()

    def refused(self, candidate, *, reason="Refusing"):
        """Resume candidate and check that it failed, with a failure row naming reason, and that no file was written."""
        manifest = self.resume(candidate)
        self.assertEqual(manifest["assets"], [])
        self.assertEqual([failure["title"] for failure in manifest["failures"]], [candidate["file_title"]])
        self.assertIn(reason, manifest["failures"][0]["error"])
        self.assertEqual(self.files(), [])
        return manifest

    def test_only_https_urls_under_the_wiki_image_path_are_requested(self):
        payload = png("red")

        class EveryHostAnswers(dict):
            def get(self, url, default=None):
                return payload

        for source, url in (
            ("bwiki", "http://patchwiki.biligame.com/images/deemo/a/ab/Art.png"),
            ("bwiki", "https://evil.example.test/images/deemo/a/ab/Art.png"),
            ("bwiki", "http://127.0.0.1:8080/images/deemo/a/ab/Art.png"),
            ("bwiki", "https://patchwiki.biligame.com/images/otherwiki/a/ab/Art.png"),
            ("bwiki", FANDOM_IMAGES + "a/ab/Art.png"),  # the other wiki's image path
            ("fandom", "http://static.wikia.nocookie.net/deemo/images/a/ab/Art.png/revision/latest?cb=1"),
        ):
            with self.subTest(source=source, url=url):
                offered = self.offer("Art.png", payload)
                candidate = {**offered, "source": source, "info": {**offered["info"], "url": url}}
                self.serve(EveryHostAnswers())  # the host would serve a valid image of the published size
                self.refused(candidate)
                self.assertEqual(self.web.calls, [])

    def test_a_redirect_is_followed_only_while_it_stays_under_the_image_path(self):
        payload = png("red")
        start, inside = BWIKI_IMAGES + "a/ab/Art.png", BWIKI_IMAGES + "b/bc/Art.png"
        for location in (
            "https://evil.example.test/images/deemo/a/ab/Art.png",
            "http://patchwiki.biligame.com/images/deemo/b/bc/Art.png",  # a downgrade to plain http
            "../../../otherwiki/a/ab/Art.png",  # relative: /images/otherwiki/a/ab/Art.png
            "https://patchwiki.biligame.com:8443/images/deemo/b/bc/Art.png",
        ):
            with self.subTest(location=location):
                offered = self.offer("Art.png", payload)
                candidate = {**offered, "info": {**offered["info"], "url": start}}
                target = urljoin(start, location)
                self.serve({start: (302, b"", {"Location": location}), target: payload})
                self.refused(candidate)
                self.assertEqual(self.web.calls, [start])
        # A redirect that stays on the image path is followed by hand, and the record names both URLs.
        self.serve({start: (301, b"", {"Location": "/images/deemo/b/bc/Art.png"}), inside: payload})
        offered = self.offer("Art.png", payload)
        (record,) = self.resume({**offered, "info": {**offered["info"], "url": start}})["assets"]
        self.assertEqual((record["download_url"], record["resolved_download_url"]), (start, inside))
        self.assertEqual(self.web.calls, [start, inside])
        self.assertEqual(self.files(), [record["path"]])
        self.assertEqual(self.web.options, [{"allow_redirects": False, "stream": True}] * len(self.web.calls))

    def test_a_redirect_loop_is_cut_off(self):
        start = BWIKI_IMAGES + "a/ab/Art.png"
        offered = self.offer("Art.png", png("red"))
        self.serve({start: (302, b"", {"Location": start})})
        self.refused({**offered, "info": {**offered["info"], "url": start}}, reason="redirects")
        self.assertEqual(self.web.calls, [start] * (fetch.MAX_REDIRECTS + 1))

    def test_a_response_over_the_size_limit_is_refused_before_anything_is_written(self):
        self.assertEqual(fetch.MAX_IMAGE_BYTES, 64 * 1024 * 1024)
        payload = png("red")
        candidate = self.offer("Art.png", payload)
        (url,) = fetch.download_urls(candidate)
        with patch.object(fetch, "MAX_IMAGE_BYTES", len(payload) - 1):
            # Streamed: the read stops at the chunk that crosses the limit.
            self.serve({url: payload + bytes(3 * fetch.CHUNK_BYTES)})
            self.refused(candidate, reason="limit")
            (response,) = self.web.responses
            self.assertLessEqual(response.consumed, fetch.CHUNK_BYTES)
            # Declared: a Content-Length over the limit is refused before the body is read.
            self.serve({url: (200, payload, {"Content-Type": "image/png", "Content-Length": str(len(payload))})})
            self.refused(candidate, reason="limit")
            (response,) = self.web.responses
            self.assertEqual(response.consumed, 0)
        self.serve({url: payload})
        with patch.object(fetch, "MAX_IMAGE_BYTES", len(payload)):
            (record,) = self.resume(candidate)["assets"]
        self.assertEqual((record["bytes"], self.files()), (len(payload), [record["path"]]))

    def test_the_wiki_image_paths_still_download(self):
        bwiki = self.offer("Art.png", png("red"))
        upload = png("green")
        fandom_url = FANDOM_IMAGES + "a/ab/Song.png/revision/latest?cb=20210901000000"
        fandom = {
            "source": "fandom",
            "file_title": "File:Song.png",
            "info": {
                **image_info(64, 48, "Song.png"),
                "url": fandom_url,
                "size": len(upload),
                "sha1": hashlib.sha1(upload).hexdigest(),
            },
            "kind": "song_art",
            "song_titles": ["Song"],
            "collections": [],
            "related_pages": [],
        }
        self.web.routes[FANDOM_IMAGES + "a/ab/Song.png?format=original"] = upload
        records = {record["title"]: record for record in self.resume(bwiki, fandom)["assets"]}
        self.assertEqual(sorted(records), ["Art", "Song"])
        self.assertEqual(records["Song"]["download_url"], FANDOM_IMAGES + "a/ab/Song.png?format=original")
        self.assertEqual(self.read(MANIFEST)["failures"], [])
        # Every URL a resume of the committed snapshot may request passes the same check.
        committed = json.loads((REPO / DISCOVERY).read_text(encoding="utf-8"))["candidates"]
        hinted = [{**row, "try_canonical_first": True, "try_png_first": True} for row in committed]
        refused = [
            url
            for row in committed + hinted
            for url in fetch.download_urls(row)
            if not fetch.download_allowed(row["source"], url)
        ]
        self.assertGreater(len(committed), 800)
        self.assertEqual(refused, [])

    def test_downloads_and_the_source_check_share_one_list_of_image_paths(self):
        self.assertEqual(Path(fetch.check_sources.__file__), REPO / "scripts/check_sources.py")
        url = BWIKI_IMAGES + "a/ab/Art.png"
        self.assertTrue(fetch.download_allowed("bwiki", url))
        with patch.dict(fetch.check_sources.DOWNLOAD_LOCATIONS, {"bwiki": ("images.example.test", "/deemo/")}):
            self.assertFalse(fetch.download_allowed("bwiki", url))
            self.assertTrue(fetch.download_allowed("bwiki", "https://images.example.test/deemo/a/ab/Art.png"))


class DeliveryNoteTests(FetcherRun):
    OLD_NOTE = (
        "Public full-size CDN PNG delivery fallback used after original endpoints failed; may be CDN-reencoded. "
        "Downloaded bytes preserved unchanged; see original checksum/size comparison fields."
    )

    def fandom(self, name, upload, served):
        """A Fandom candidate for upload whose original endpoints are down and whose format=png fallback serves served."""
        url = f"{FANDOM_IMAGES}a/ab/{name}/revision/latest?cb=1"
        self.web.routes[url + "&format=png"] = served
        with Image.open(io.BytesIO(upload)) as image:
            width, height = image.size
        info = {
            **image_info(width, height, name),
            "url": url,
            "size": len(upload),
            "sha1": hashlib.sha1(upload).hexdigest(),
        }
        return {
            "source": "fandom",
            "file_title": "File:" + name,
            "info": info,
            "kind": "song_art",
            "song_titles": [],
            "collections": [],
            "related_pages": [],
        }

    def test_png_fallback_identical_to_the_upload_is_not_called_reencoded(self):
        record = fetch.download(self.fandom("Longinus.png", png("red"), png("red")), {})
        self.assertTrue(record["download_url"].endswith("&format=png"))
        self.assertTrue(record["wiki_original_sha1_matches"] and record["wiki_original_size_matches"])
        self.assertNotIn("reencoded", record["delivery_note"])
        self.assertIn("SHA-1 and size match the wiki original upload", record["delivery_note"])

    def test_png_fallback_that_differs_from_the_upload_keeps_the_caveat(self):
        record = fetch.download(self.fandom("Longinus.png", png("red"), png("green")), {})
        self.assertFalse(record["wiki_original_sha1_matches"])
        self.assertIn("may be CDN-reencoded", record["delivery_note"])

    def test_verified_record_gets_the_note_its_checksums_support(self):
        candidate = self.fandom("Longinus.png", png("red"), png("red"))
        record = fetch.download(candidate, {})
        calls = len(self.web.calls)
        again = fetch.download(candidate, {record["id"]: {**record, "delivery_note": self.OLD_NOTE}})
        self.assertEqual(len(self.web.calls), calls)  # verified from the file on disk, not downloaded again
        self.assertNotEqual(again["delivery_note"], self.OLD_NOTE)
        self.assertEqual(again, record)
        self.assertEqual(list(again)[-1], "delivery_note")


class CollectionNameTests(unittest.TestCase):
    def test_spellings_that_differ_in_case_spacing_or_punctuation_merge(self):
        names = [
            "Etude collection",
            "RAC collection -1",
            "Deemo's collection Vol.1B",
            "Etude Collection",
            "RAC Collection #1",
            "Deemo's collection Vol. 1B",
            "Deemo's Collection Vol.1",
            "Etude collection",
        ]
        expected = ["Deemo's Collection Vol.1", "Deemo's collection Vol. 1B", "Etude Collection", "RAC Collection #1"]
        self.assertEqual(fetch.unique_collections(names), expected)
        self.assertEqual(fetch.unique_collections(reversed(names)), expected)

    def test_bwiki_song_art_lists_each_collection_once(self):
        # The README's Magnolia example: Fandom and BWIKI spell the same collections differently.
        selected, _, _ = discover_bwiki(
            [allimage("Magnolia.png", 1024, 1024)],
            [
                song("Magnolia", ["Deemo's Collection Vol.1", "Deemo's collection Vol. 1B"]),
                song("Magnolia", ["Deemo's collection Vol.1B"], source="bwiki"),
            ],
        )
        self.assertEqual(selected[0]["collections"], ["Deemo's Collection Vol.1", "Deemo's collection Vol. 1B"])

    def test_bwiki_cover_uses_the_bwiki_spelling_of_its_collection(self):
        # The cover's related page is a BWIKI page, whose title is BWIKI's spelling ("#" cannot appear in a title);
        # a Fandom spelling is used only for a collection that no BWIKI song page names.
        bwiki_pages = [bwiki_song_page("Song A", "Etude collection"), bwiki_song_page("Song B", "RAC collection -1")]
        fandom = [
            song("Song A", ["Etude Collection", "Aioi collection", "Aioi Collection"]),
            song("Song B", ["RAC Collection #1"]),
        ]
        inventory = [
            allimage(name, 180, 180)
            for name in ("Etude_collection.png", "RAC_collection_-1.png", "Aioi_collection.png")
        ]
        expected = {
            "Etude_collection.png": ("Etude collection", "Etude%20collection"),
            "RAC_collection_-1.png": ("RAC collection -1", "RAC%20collection%20-1"),
            "Aioi_collection.png": ("Aioi Collection", "Aioi%20Collection"),
        }
        for pages in (bwiki_pages, bwiki_pages[::-1]):
            selected, _, _ = discover_bwiki(inventory, fandom, pages)
            self.assertEqual(
                {row["info"]["name"]: (row["kind"], row["collections"], row["related_pages"]) for row in selected},
                {
                    name: ("collection_cover", [collection], ["https://wiki.biligame.com/deemo/" + page])
                    for name, (collection, page) in expected.items()
                },
            )

    def test_bwiki_cover_never_links_to_a_title_mediawiki_forbids(self):
        # "#" cannot appear in a title, so a collection spelled with it (BWIKI's own "RAC collection #4", or the
        # Fandom-only "RAC Collection #5") keeps its name but links to the cover file's stem.
        selected, _, _ = discover_bwiki(
            [allimage("RAC_collection_-4.png", 180, 180), allimage("RAC_collection_-5.png", 180, 180)],
            [song("Song C", ["RAC Collection #5"])],
            [bwiki_song_page("Song D", "RAC collection #4")],
        )
        self.assertEqual(
            {row["info"]["name"]: (row["collections"], row["related_pages"]) for row in selected},
            {
                "RAC_collection_-4.png": (
                    ["RAC collection #4"],
                    ["https://wiki.biligame.com/deemo/RAC%20collection%20-4"],
                ),
                "RAC_collection_-5.png": (
                    ["RAC Collection #5"],
                    ["https://wiki.biligame.com/deemo/RAC%20collection%20-5"],
                ),
            },
        )

    def test_fandom_merges_spellings_across_song_pages(self):
        selected, _, _ = discover_fandom(
            [
                fandom_page("Song A", "{{Return|Etude collection}}", "File:Shared.png"),
                fandom_page("Song B", "{{Return|Etude Collection}}", "File:Shared.png"),
            ],
            {"File:Shared.png": image_info(1024, 1024)},
        )
        self.assertEqual(selected[0]["collections"], ["Etude Collection"])


class CollectionAliasTests(FetcherRun):
    def test_every_record_shows_one_spelling_and_keeps_the_others_as_aliases(self):
        songs = [
            song("A", ["RAC Collection #1", "Etude Collection"]),
            song("B", ["RAC collection -1"], source="bwiki"),
            song("C", ["Etude collection"]),
            song("D", ["Sakuzyo collection"]),
        ]
        candidates = [
            {"file_title": "1", "collections": ["RAC collection -1"]},
            {
                "file_title": "2",
                "collections": ["Etude Collection", "RAC Collection #1"],
                "collection_aliases": ["Old"],
            },
            {"file_title": "3", "collections": ["Sakuzyo collection", "Book of Alice"], "collection_aliases": ["Old"]},
            {"file_title": "4", "collections": ["Book of alice", "etude collection"]},
        ]
        result = fetch.with_collection_aliases(candidates, songs)
        # The BWIKI cover's "RAC collection -1" reads as every other record's "RAC Collection #1": the first spelling in
        # code-point order of all that the song pages and the candidates use. A cover's own spelling is one of them.
        self.assertEqual(
            [row["collections"] for row in result],
            [
                ["RAC Collection #1"],
                ["Etude Collection", "RAC Collection #1"],
                ["Sakuzyo collection", "Book of Alice"],
                ["Book of Alice", "Etude Collection"],
            ],
        )
        self.assertEqual(
            [row.get("collection_aliases") for row in result],
            [
                ["RAC collection -1"],
                ["Etude collection", "RAC collection -1", "etude collection"],
                ["Book of alice"],
                ["Book of alice", "Etude collection", "etude collection"],
            ],
        )
        self.assertEqual(
            {tuple(row)[:2] for row in result}, {("file_title", "collections")}
        )  # collections stays in place
        self.assertEqual(candidates[1]["collection_aliases"], ["Old"])  # the input is left as it was
        self.assertEqual(fetch.with_collection_aliases(result, songs), result)
        shown = {}
        for row in result:
            for name in row["collections"]:
                shown.setdefault(fetch.normalized(name), set()).add(name)
        self.assertEqual({key: len(names) for key, names in shown.items() if len(names) > 1}, {})

    def test_discovery_stores_the_aliases_with_the_snapshot(self):
        candidate = self.offer("Art.png", png("red"), collections=["Etude Collection"])
        with (
            patch.object(fetch, "discover_fandom", return_value=([], [song("Art", ["Etude Collection"])], {})),
            patch.object(
                fetch, "discover_bwiki", return_value=([candidate], [song("Art", ["Etude collection"], "bwiki")], {})
            ),
        ):
            self.run_main("--metadata-only")
        self.assertEqual(self.read(DISCOVERY)["candidates"][0]["collection_aliases"], ["Etude collection"])

    def test_records_keep_the_snapshot_aliases_in_a_stable_place(self):
        offered = self.offer(
            "Art.png", png("red"), collections=["RAC Collection #1"], collection_aliases=["RAC collection -1"]
        )
        candidate = {**offered, "info": {**offered["info"], "sha1": "f" * 40}}  # a checksum caveat adds a delivery_note
        first = self.resume(candidate)["assets"][0]
        self.assertEqual(first["collection_aliases"], ["RAC collection -1"])
        self.assertEqual(list(first)[-2:], ["collection_aliases", "delivery_note"])
        calls = len(self.web.calls)
        again = self.resume(candidate)["assets"][0]
        self.assertEqual(len(self.web.calls), calls)  # verified from the file on disk, not downloaded again
        self.assertEqual(list(again.items()), list(first.items()))
        plain = {key: value for key, value in candidate.items() if key != "collection_aliases"}
        self.assertNotIn("collection_aliases", self.resume(plain)["assets"][0])


class OrderingTests(FetcherRun):
    # Discovers one BWIKI cover in a fresh interpreter, where set iteration order follows PYTHONHASHSEED.
    CHILD = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("fetch_wikis", sys.argv[1])
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
fetch.category = lambda source, title: []
fetch.pages = lambda source, titles: []
fetch.allimages = lambda source: [{"name": "Aioi_collection.png", "title": "Aioi collection.png", "width": 180, "height": 180, "mime": "image/png"}]
songs = [{"title": "A", "page_url": "a", "collections": ["Aioi collection"]}, {"title": "B", "page_url": "b", "collections": ["Aioi Collection"]}]
cover = fetch.discover_bwiki(songs)[0][0]
print(json.dumps(cover["collections"] + cover["related_pages"]))
"""

    def test_failures_are_sorted_whatever_the_completion_order(self):
        candidates = [self.offer(name, png("red")) for name in ("Alpha.png", "Beta.png", "Gamma.png", "Delta.png")]
        self.web.routes.clear()  # every download fails

        def reverse_completion(futures):
            futures = list(futures)
            concurrent.futures.wait(futures)
            return reversed(futures)

        with patch.object(fetch, "as_completed", reverse_completion):
            failures = self.resume(*candidates, workers=4)["failures"]
        self.assertEqual(
            [failure["title"] for failure in failures],
            ["文件:Alpha.png", "文件:Beta.png", "文件:Delta.png", "文件:Gamma.png"],
        )

    def test_cover_collection_spelling_does_not_depend_on_hash_order(self):
        outputs = set()
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        with tempfile.TemporaryDirectory() as empty:
            for seed in range(12):
                result = subprocess.run(
                    [sys.executable, "-s", "-c", self.CHILD, str(SCRIPT)],
                    cwd=empty,
                    capture_output=True,
                    env={**env, "PYTHONHASHSEED": str(seed)},
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-600:])
                outputs.add(result.stdout)
        self.assertEqual(
            [json.loads(output) for output in outputs],
            [["Aioi Collection", "https://wiki.biligame.com/deemo/Aioi%20Collection"]],
        )

    def test_fandom_candidate_lists_do_not_depend_on_page_order(self):
        pages = [fandom_page("Song B", "", "File:Shared.png"), fandom_page("Song A", "", "File:Shared.png")]
        infos = {"File:Shared.png": image_info(1024, 1024)}
        forward, backward = discover_fandom(pages, infos)[0][0], discover_fandom(pages[::-1], infos)[0][0]
        self.assertEqual(forward, backward)
        self.assertEqual(forward["song_titles"], ["Song A", "Song B"])


class DiscoveryFilterTests(unittest.TestCase):
    def test_bwiki_skips_ui_sprites_like_the_fandom_path(self):
        inventory = [
            allimage("Exotic_Collections_Titletab.png", 70, 47),  # imported as a "cover" on 2026-10-08
            allimage("DEEMO_II_collection.png", 512, 512),
            allimage("20210901_collection.png", 512, 512),  # ^\d{8}  only matches the spaced title form
            allimage("Etude_collection.png", 180, 180),
            allimage("Magnolia.png", 1024, 1024),
        ]
        selected, _, stats = discover_bwiki(inventory, [song("Magnolia", ["Etude Collection"])])
        self.assertEqual(
            {row["info"]["name"]: row["kind"] for row in selected},
            {"Etude_collection.png": "collection_cover", "Magnolia.png": "song_art"},
        )
        self.assertEqual(stats["excluded_large_images"], [])

    def test_bwiki_collection_covers_need_a_minimum_edge(self):
        selected, _, _ = discover_bwiki(
            [allimage("Spring_selection.png", 180, 142), allimage("Tiny_collection.png", 99, 300)]
        )
        self.assertEqual([row["info"]["name"] for row in selected], ["Spring_selection.png"])

    def test_fandom_skips_icon_sized_collection_covers(self):
        page = fandom_page(
            "Song A",
            "{{Song|img=Song A.png}}{{Return|Etude Collection}}",
            "File:Song A.png",
            "File:Tiny booksprite.png",
            "File:Etude booksprites.png",
        )
        infos = {
            "File:Song A.png": image_info(1024, 1024),
            "File:Tiny booksprite.png": image_info(64, 64),
            "File:Etude booksprites.png": image_info(512, 512),
        }
        selected, _, stats = discover_fandom([page], infos)
        self.assertEqual(
            {row["file_title"]: row["kind"] for row in selected},
            {"File:Song A.png": "song_art", "File:Etude booksprites.png": "collection_cover"},
        )
        self.assertEqual([row["title"] for row in stats["excluded"]], ["File:Tiny booksprite.png"])


class ContinuationTests(unittest.TestCase):
    """MediaWiki stops a query at its result-size limit and returns `continue`; the rest arrives in later responses."""

    def test_imageinfo_follows_the_continuation(self):
        page = fandom_page("Song A", "{{Song|img=Alpha.png}}", "File:Alpha.png", "File:Beta.png", "File:Gamma.png")
        names = ("Alpha.png", "Beta.png", "Gamma.png")
        requested = []

        def api(source, **params):
            requested.append(params)
            # The first response holds Alpha's imageinfo only, the continued one the other two.
            listed = names[:1] if "iicontinue" not in params else names[1:]
            pages = {
                str(index): {
                    "title": "File:" + name,
                    **({"imageinfo": [image_info(1024, 1024, name)]} if name in listed else {}),
                }
                for index, name in enumerate(names)
            }
            more = (
                {}
                if "iicontinue" in params
                else {"continue": {"iicontinue": "Beta.png|20210901000000", "continue": "||"}}
            )
            return {**more, "query": {"pages": pages}}

        with (
            patch.object(
                fetch,
                "category",
                lambda source, title: [{"title": "Song A", "ns": 0}] if title == "Category:Songs" else [],
            ),
            patch.object(fetch, "pages", lambda source, titles: [page] if titles == ["Song A"] else []),
            patch.object(fetch, "api", api),
        ):
            selected, _, stats = fetch.discover_fandom()
        self.assertEqual([row["file_title"] for row in selected], ["File:" + name for name in names])
        self.assertEqual(stats["excluded"], [])
        self.assertEqual([params.get("iicontinue") for params in requested], [None, "Beta.png|20210901000000"])

    def test_pages_keep_revisions_that_arrive_in_a_continuation(self):
        complete = {
            "A": fandom_page("A", "{{Song|Artist=Composer A}}"),
            "B": fandom_page("B", "{{Song|Artist=Composer B}}"),
        }

        def without_revisions(title):
            return {key: value for key, value in complete[title].items() if key != "revisions"}

        def api(source, **params):
            if "rvcontinue" not in params:
                pages = {"1": complete["A"], "2": without_revisions("B")}
                return {"continue": {"rvcontinue": "2|0", "continue": "||"}, "query": {"pages": pages}}
            return {"query": {"pages": {"1": without_revisions("A"), "2": complete["B"]}}}

        with patch.object(fetch, "api", api):
            pages = fetch.pages("fandom", ["A", "B"])
        self.assertEqual(
            {page["title"]: fetch.song_metadata("fandom", page).get("composer") for page in pages},
            {"A": "Composer A", "B": "Composer B"},
        )


class EncodingTests(unittest.TestCase):
    def test_resume_reads_utf8_manifests_under_an_ascii_locale(self):
        """The manifests are UTF-8 (BWIKI titles start with 文件:); reading them must not depend on the locale."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            title = "文件:Magnolia.png"
            dump(
                root / "data/sources/wikis.json",
                {
                    "schema_version": 1,
                    "fetched_at": "2026-01-01T00:00:00Z",
                    "failures": [],
                    "sources": [{**fetch.SOURCES["bwiki"], "status": "complete"}],
                    "assets": [{"id": asset_id("bwiki", title), "source_id": "wikis:bwiki", "title": "Magnolia"}],
                },
            )
            dump(
                root / "data/sources/wiki-discovery.json",
                {
                    "schema_version": 1,
                    "fetched_at": "2026-01-01T00:00:00Z",
                    "candidates": [{"source": "bwiki", "file_title": title, "kind": "song_art", "info": {}}],
                },
            )
            code = (
                "import importlib.util, sys\nfrom pathlib import Path\n"
                "spec = importlib.util.spec_from_file_location('fetch_wikis', sys.argv[1])\n"
                "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
                "module.ROOT = Path(sys.argv[2])\nsys.argv = ['fetch_wikis.py', '--resume', '--metadata-only']\n"
                "module.main()\n"
            )
            # LC_ALL=C with UTF-8 mode off makes the locale encoding ASCII, like cp1252/GBK it cannot decode 文件.
            env = {key: value for key, value in os.environ.items() if not key.startswith("LC_")}
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8=0", "-c", code, str(SCRIPT), str(root)],
                env={**env, "LC_ALL": "C"},
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr[-600:])
            self.assertIn("Resuming saved discovery: 1 candidates", result.stdout)


if __name__ == "__main__":
    unittest.main()
