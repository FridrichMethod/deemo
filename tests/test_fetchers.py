"""Offline checks for the artist and archive fetchers (no network: HTTP is faked or served on loopback)."""

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artists = load("fetch_artists")
archives = load("fetch_archives")


def png(color, size=(4, 4)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def response(url, status=200, body=b"", headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = body if isinstance(body, bytes) else body.encode("utf-8")
    result.headers.update(headers or {})
    result.url = url
    result.encoding = "utf-8"
    return result


class FakeWeb:
    """Serves canned responses by exact URL; anything else fails like an unreachable host.

    Like requests, it follows redirects itself (with no host check) unless allow_redirects=False,
    so a fetcher that stops passing allow_redirects=False fails the redirect tests.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requested = []
        self.lock = threading.Lock()

    def __call__(self, url, allow_redirects=True, **kwargs):
        for _ in range(10):
            with self.lock:
                self.requested.append(url)
            route = self.routes.get(url)
            if route is None:
                raise requests.ConnectionError(f"offline test: {url}")
            status, body, headers = route if isinstance(route, tuple) else (200, route, {})
            result = response(url, status, body, headers)
            if not (allow_redirects and result.is_redirect):
                return result
            url = urljoin(url, result.headers["location"])
        raise requests.TooManyRedirects(url)

    get = __call__


class TempRoot(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "data/sources").mkdir(parents=True)

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative

    def write_manifest(self, name, manifest):
        path = self.root / f"data/sources/{name}.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def read_manifest(self, name):
        return json.loads((self.root / f"data/sources/{name}.json").read_text(encoding="utf-8"))

    def archived_files(self):
        return {path.relative_to(self.root).as_posix() for path in (self.root / "assets").rglob("*") if path.is_file()}


# ---------------------------------------------------------------- fetch_artists

PIXIV = {"snowegg": {"artist": "SnowEgg", "user_id": "7", "posts": {"1": "Test Collection"}}}
PAGE_URL = "https://www.pixiv.net/en/artworks/1"
OLD_P0 = "https://i.pximg.net/img-original/img/2021/01/01/00/00/00/1_p0.png"
NEW_P0 = "https://i.pximg.net/img-original/img/2022/01/01/00/00/00/1_p0.png"


def pixiv_routes(pages):
    metadata = {"error": False, "body": {"userId": "7", "userName": "SnowEgg", "illustTitle": "Live title",
                                         "createDate": "2021-01-01T00:00:00+00:00", "uploadDate": "2022-01-01T00:00:00+00:00",
                                         "illustComment": "", "tags": {"tags": []}}}
    body = [{"urls": {"original": url}, "width": 4, "height": 4} for url in pages]
    return {"https://www.pixiv.net/ajax/illust/1": json.dumps(metadata),
            "https://www.pixiv.net/ajax/illust/1/pages": json.dumps({"error": False, "body": body})}


class ArtistsTests(TempRoot):
    def setUp(self):
        super().setUp()
        for target, value in (("ROOT", self.root), ("PIXIV_POSTS", PIXIV)):
            patcher = patch.object(artists, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("collect_jimdo", "collect_tumblr", "collect_reference"):
            patcher = patch.object(artists, name, lambda *args, **kwargs: None)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(artists.time, "sleep", lambda seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def previous_record(self, index, data, url):
        path = self.put(f"assets/public/artists/snowegg/pixiv-1-p{index:02d}.png", data)
        return {"id": f"artists:pixiv:1:p{index}", "source_id": "artists:pixiv:1", "artist": "SnowEgg",
                "title": f"Test Collection — page {index + 1}", "kind": "song_art", "page_url": PAGE_URL,
                "download_url": url, "path": path, "width": 4, "height": 4, "format": "PNG", "mode": "RGB",
                "bytes": len(data), "sha256": sha(data), "fetched_at": "2026-01-01T00:00:00+00:00", "game": "DEEMO"}

    def seed(self):
        self.old = {0: png("red"), 1: png("green")}
        records = [self.previous_record(0, self.old[0], OLD_P0),
                   self.previous_record(1, self.old[1], OLD_P0.replace("_p0", "_p1"))]
        source = {"id": "artists:pixiv:1", "name": "Archived title", "url": PAGE_URL, "artist": "SnowEgg",
                  "status": "complete", "platform": "Pixiv", "access": "public_without_login",
                  "artist_display_name": "SnowEgg", "description": "kept", "asset_count": 2}
        self.write_manifest("artists", {"schema_version": 1, "sources": [source], "assets": records, "failures": []})

    def run_main(self, web, *flags):
        with patch.object(artists.requests, "get", web), patch.object(sys, "argv", ["fetch_artists.py", "--workers", "2", *flags]), \
                contextlib.redirect_stdout(io.StringIO()):
            return artists.main()

    def test_reupload_keeps_old_file_and_record_as_superseded(self):
        self.seed()
        new = png("blue")
        web = FakeWeb({**pixiv_routes([NEW_P0]), NEW_P0: new})
        self.assertEqual(self.run_main(web), 0)
        old_path = self.root / "assets/public/artists/snowegg/pixiv-1-p00.png"
        self.assertEqual(old_path.read_bytes(), self.old[0], "archived bytes were overwritten in place")
        assets = {asset["id"]: asset for asset in self.read_manifest("artists")["assets"]}
        canonical = assets["artists:pixiv:1:p0"]
        self.assertEqual(canonical["sha256"], sha(new))
        self.assertEqual(canonical["path"], f"assets/public/artists/snowegg/pixiv-1-p00-{sha(new)[:12]}.png")
        self.assertEqual((self.root / canonical["path"]).read_bytes(), new)
        superseded = assets[f"artists:pixiv:1:p0:{sha(self.old[0])[:12]}"]
        self.assertEqual(superseded["upstream_status"], "superseded")
        self.assertEqual(superseded["superseded_by"], "artists:pixiv:1:p0")
        self.assertEqual(superseded["path"], "assets/public/artists/snowegg/pixiv-1-p00.png")
        self.assertEqual(superseded["sha256"], sha(self.old[0]))
        self.assertEqual(assets["artists:pixiv:1:p1"]["upstream_status"], "removed")
        self.assertTrue((self.root / assets["artists:pixiv:1:p1"]["path"]).is_file())

        # A rerun against the same upstream reuses the files and leaves the manifest unchanged.
        first = self.read_manifest("artists")
        web = FakeWeb(pixiv_routes([NEW_P0]))
        self.assertEqual(self.run_main(web), 0)
        second = self.read_manifest("artists")
        self.assertNotIn(NEW_P0, web.requested)
        first.pop("fetched_at"), second.pop("fetched_at")
        self.assertEqual(first, second)

    def test_lost_file_is_refetched_without_a_superseded_record(self):
        self.seed()
        (self.root / "assets/public/artists/snowegg/pixiv-1-p00.png").unlink()  # missing from the checkout
        new = png("blue")
        web = FakeWeb({**pixiv_routes([OLD_P0, OLD_P0.replace("_p0", "_p1")]), OLD_P0: new})
        self.assertEqual(self.run_main(web), 0)
        assets = self.read_manifest("artists")["assets"]
        self.assertEqual([(a["id"], a["path"], a["sha256"], a.get("upstream_status")) for a in assets],
                         [("artists:pixiv:1:p0", "assets/public/artists/snowegg/pixiv-1-p00.png", sha(new), None),
                          ("artists:pixiv:1:p1", "assets/public/artists/snowegg/pixiv-1-p01.png", sha(self.old[1]), None)])
        for asset in assets:
            self.assertEqual(sha((self.root / asset["path"]).read_bytes()), asset["sha256"], asset["id"])

        # Green replaces blue, so blue is kept as superseded at p00.png. When p00.png is then lost and
        # upstream serves white, white takes that path and the blue record, whose bytes are gone, is dropped.
        p1_url, green_url, white_url = OLD_P0.replace("_p0", "_p1"), NEW_P0, NEW_P0.replace("/2022/", "/2023/")
        web.routes.update({**pixiv_routes([green_url, p1_url]), green_url: png("green")})
        self.assertEqual(self.run_main(web), 0)
        (self.root / "assets/public/artists/snowegg/pixiv-1-p00.png").unlink()
        web.routes.update({**pixiv_routes([white_url, p1_url]), white_url: png("white")})
        self.assertEqual(self.run_main(web), 0)
        assets = self.read_manifest("artists")["assets"]
        green = f"assets/public/artists/snowegg/pixiv-1-p00-{sha(png('green'))[:12]}.png"
        self.assertEqual([(a["id"], a["path"], a["sha256"]) for a in assets],
                         [("artists:pixiv:1:p0", "assets/public/artists/snowegg/pixiv-1-p00.png", sha(png("white"))),
                          (f"artists:pixiv:1:p0:{sha(png('green'))[:12]}", green, sha(png("green"))),
                          ("artists:pixiv:1:p1", "assets/public/artists/snowegg/pixiv-1-p01.png", sha(self.old[1]))])
        for asset in assets:
            self.assertEqual(sha((self.root / asset["path"]).read_bytes()), asset["sha256"], asset["id"])

    def test_lost_superseded_file_leaves_the_canonical_record_on_its_copy(self):
        self.seed()
        p1_url = OLD_P0.replace("_p0", "_p1")
        web = FakeWeb({**pixiv_routes([NEW_P0, p1_url]), NEW_P0: png("blue"), p1_url: self.old[1]})
        self.assertEqual(self.run_main(web), 0)

        def summary():
            return [(a["id"], a["path"], a["sha256"], a.get("upstream_status")) for a in self.read_manifest("artists")["assets"]]

        before = summary()
        lost = "assets/public/artists/snowegg/pixiv-1-p00.png"
        self.assertEqual([row[:2] for row in before],
                         [("artists:pixiv:1:p0", f"assets/public/artists/snowegg/pixiv-1-p00-{sha(png('blue'))[:12]}.png"),
                          (f"artists:pixiv:1:p0:{sha(self.old[0])[:12]}", lost),
                          ("artists:pixiv:1:p1", "assets/public/artists/snowegg/pixiv-1-p01.png")])
        # The superseded red file is lost and upstream still serves blue, from the cache or downloaded
        # again: nothing moves, so no copy is left without a record.
        (self.root / lost).unlink()
        for flags in ((), ("--refresh",)):
            self.assertEqual(self.run_main(web, *flags), 0)
            self.assertEqual(summary(), before, flags)
            self.assertFalse((self.root / lost).exists(), "a second copy of blue was written")
            self.assertEqual({row[1] for row in before} - {lost}, self.archived_files(), flags)

    def test_revert_deduplicated_to_another_page_keeps_every_file_referenced(self):
        red, blue = png("red"), png("blue")
        p1_url = OLD_P0.replace("_p0", "_p1")
        p0 = self.previous_record(0, red, OLD_P0)
        p1 = self.previous_record(1, red, p1_url)  # red at pixiv-1-p01.png, now the superseded version
        blue_path = self.put(f"assets/public/artists/snowegg/pixiv-1-p01-{sha(blue)[:12]}.png", blue)
        assets = [p0, {**p1, "path": blue_path, "sha256": sha(blue), "bytes": len(blue)},
                  {**p1, "id": f"{p1['id']}:{sha(red)[:12]}", "upstream_status": "superseded", "superseded_by": p1["id"]}]
        self.write_manifest("artists", {"schema_version": 1, "sources": [], "assets": assets, "failures": []})
        # Upstream reverts page 2 to red, the bytes of page 1, so its record now points at pixiv-1-p00.png.
        web = FakeWeb({**pixiv_routes([OLD_P0, p1_url]), OLD_P0: red, p1_url: red})
        self.assertEqual(self.run_main(web, "--refresh"), 0)
        result = {asset["id"]: asset for asset in self.read_manifest("artists")["assets"]}
        self.assertEqual(sorted(result), sorted([p0["id"], p1["id"], f"{p1['id']}:{sha(blue)[:12]}", f"{p1['id']}:{sha(red)[:12]}"]))
        self.assertEqual((result[p1["id"]]["path"], result[p1["id"]]["duplicate_of"]), (p0["path"], p0["id"]))
        files = {path.relative_to(self.root).as_posix() for path in (self.root / "assets").rglob("*") if path.is_file()}
        self.assertEqual({asset["path"] for asset in result.values()}, files, "an archived file is no longer referenced")

    def test_duplicate_stem_stops_main_before_any_download(self):
        self.seed()
        before = (self.root / "data/sources/artists.json").read_bytes()

        def collect_jimdo(manifest, jobs, previous=()):
            jobs.append({"id": "artists:jimdo:kolokolsan:clash", "source_id": "artists:jimdo:kolokolsan",
                         "directory": "snowegg", "stem": "pixiv-1-p00", "page_url": artists.JIMDO_URL,
                         "download_url": LIGHTBOX.format(key="i0000000000000001").replace("/transf/dimension=2048x2048:format=png/", "/transf/none/")})

        web = FakeWeb({**pixiv_routes([NEW_P0]), NEW_P0: png("blue")})
        with patch.object(artists, "collect_jimdo", collect_jimdo), self.assertRaisesRegex(SystemExit, "snowegg/pixiv-1-p00"):
            self.run_main(web)
        self.assertEqual(web.requested, list(pixiv_routes([NEW_P0])), "a download started before the duplicate check")
        self.assertEqual((self.root / "data/sources/artists.json").read_bytes(), before)

    def test_removed_record_stays_removed_when_its_source_fails(self):
        self.seed()
        manifest = self.read_manifest("artists")
        manifest["assets"][1]["upstream_status"] = "removed"
        self.write_manifest("artists", manifest)
        self.assertEqual(self.run_main(FakeWeb()), 1)
        self.assertEqual([(a["id"], a["upstream_status"]) for a in self.read_manifest("artists")["assets"]],
                         [("artists:pixiv:1:p0", "fetch_failed"), ("artists:pixiv:1:p1", "removed")])

    def test_record_of_an_unconfigured_source_keeps_its_source_row(self):
        self.seed()
        manifest = self.read_manifest("artists")
        data = png("yellow")
        retired = {"id": "artists:pixiv:2", "name": "Retired", "url": "https://www.pixiv.net/en/artworks/2", "artist": "SnowEgg",
                   "status": "complete", "platform": "Pixiv", "access": "public_without_login", "asset_count": 1}
        manifest["sources"].append(retired)
        manifest["assets"].append({**manifest["assets"][0], "id": "artists:pixiv:2:p0", "source_id": retired["id"],
                                   "path": self.put("assets/public/artists/snowegg/pixiv-2-p00.png", data),
                                   "download_url": OLD_P0.replace("1_p0", "2_p0"), "bytes": len(data), "sha256": sha(data)})
        self.write_manifest("artists", manifest)
        self.assertEqual(self.run_main(FakeWeb(pixiv_routes([OLD_P0, OLD_P0.replace("_p0", "_p1")]))), 0)
        result = self.read_manifest("artists")
        self.assertIn(retired, result["sources"])
        self.assertEqual({a["id"]: a.get("upstream_status") for a in result["assets"]},
                         {"artists:pixiv:1:p0": None, "artists:pixiv:1:p1": None, "artists:pixiv:2:p0": "removed"})

    def test_discovery_failure_keeps_previous_assets(self):
        self.seed()
        self.assertEqual(self.run_main(FakeWeb()), 1)
        manifest = self.read_manifest("artists")
        assets = {asset["id"]: asset for asset in manifest["assets"]}
        self.assertEqual(set(assets), {"artists:pixiv:1:p0", "artists:pixiv:1:p1"})
        self.assertTrue(all(asset["upstream_status"] == "fetch_failed" for asset in assets.values()))
        source = manifest["sources"][0]
        self.assertEqual((source["status"], source["name"], source["description"], source["asset_count"]),
                         ("failed", "Archived title", "kept", 2))
        self.assertEqual([(f["source_id"], f["url"]) for f in manifest["failures"]], [("artists:pixiv:1", PAGE_URL)])

    def test_failures_are_written_in_a_stable_order(self):
        posts = {"snowegg": {**PIXIV["snowegg"], "posts": {"3": "C", "2": "B", "1": "A"}}}
        with patch.object(artists, "PIXIV_POSTS", posts):
            self.assertEqual(self.run_main(FakeWeb()), 1)
        self.assertEqual([f["source_id"] for f in self.read_manifest("artists")["failures"]],
                         ["artists:pixiv:1", "artists:pixiv:2", "artists:pixiv:3"])

    def test_download_failure_marks_record_fetch_failed(self):
        self.seed()
        web = FakeWeb({**pixiv_routes([NEW_P0, OLD_P0.replace("_p0", "_p1")]), NEW_P0: (503, b"", {})})
        self.assertEqual(self.run_main(web), 1)
        manifest = self.read_manifest("artists")
        assets = {asset["id"]: asset for asset in manifest["assets"]}
        self.assertEqual(assets["artists:pixiv:1:p0"]["upstream_status"], "fetch_failed")
        self.assertNotIn("upstream_status", assets["artists:pixiv:1:p1"])
        self.assertEqual(manifest["sources"][0]["status"], "partial")
        self.assertEqual(manifest["failures"][0]["asset_id"], "artists:pixiv:1:p0")

    def test_dedup_primary_does_not_depend_on_completion_order(self):
        data = png("purple")
        jobs = [{"id": f"artists:pixiv:9:p{i}", "source_id": "artists:pixiv:9", "directory": "x",
                 "stem": f"pixiv-9-p{i:02d}", "download_url": f"https://i.pximg.net/{i}.png", "page_url": PAGE_URL}
                for i in (0, 1)]
        for slow in ("artists:pixiv:9:p0", "artists:pixiv:9:p1"):
            def fake_download(job, cache, refresh, slow=slow):
                threading.Event().wait(0.2 if job["id"] == slow else 0)  # time.sleep is patched out in setUp
                return job, data, "2026-01-01T00:00:00+00:00"
            manifest = {"assets": [], "failures": []}
            with patch.object(artists, "download", fake_download), contextlib.redirect_stdout(io.StringIO()):
                artists.fetch_all(manifest, list(reversed(jobs)), {}, False, 2)
            by_id = {asset["id"]: asset for asset in manifest["assets"]}
            self.assertEqual(by_id["artists:pixiv:9:p0"]["path"], "assets/public/artists/x/pixiv-9-p00.png")
            self.assertNotIn("duplicate_of", by_id["artists:pixiv:9:p0"])
            self.assertEqual(by_id["artists:pixiv:9:p1"]["duplicate_of"], "artists:pixiv:9:p0")

    def test_download_refuses_other_hosts_and_redirects(self):
        job = {"id": "artists:pixiv:1:p0", "source_id": "artists:pixiv:1", "page_url": PAGE_URL}
        # urllib3 reads the backslash as the start of the path, so the second URL goes to 169.254.169.254.
        for url in ("https://i.pximg.net.evil.example/1_p0.png", "https://169.254.169.254\\@i.pximg.net/img-original/1_p0.png",
                    "http://i.pximg.net/img-original/1_p0.png"):
            web = FakeWeb()
            with patch.object(artists.requests, "get", web), self.assertRaisesRegex(ValueError, "expected hosts"):
                artists.download({**job, "download_url": url}, {}, False)
            self.assertEqual(web.requested, [])
        for location in ("http://169.254.169.254/latest", "https://169.254.169.254\\@i.pximg.net/1_p0.png"):
            web = FakeWeb({NEW_P0: (302, b"", {"Location": location})})
            with patch.object(artists.requests, "get", web), self.assertRaisesRegex(ValueError, "expected hosts"):
                artists.download({**job, "download_url": NEW_P0}, {}, False)
            self.assertEqual(web.requested, [NEW_P0])
        # A relative Location is resolved against the hop that sent it.
        moved = "https://i.pximg.net/img-master/1_p0.png"
        web = FakeWeb({NEW_P0: (302, b"", {"Location": "/img-master/1_p0.png"}), moved: png("red")})
        with patch.object(artists.requests, "get", web):
            self.assertEqual(artists.download({**job, "download_url": NEW_P0}, {}, False)[1], png("red"))
        self.assertEqual(web.requested, [NEW_P0, moved])


class TumblrArtistTests(unittest.TestCase):
    # Many posts inserted out of order, so neither dict nor set iteration order is sorted by chance.
    POSTS = {post: f"Collection {post}" for post in ("907", "15", "311", "42", "7", "128", "64", "999", "3", "250", "76", "501")}

    def setUp(self):
        patcher = patch.object(artists, "TUMBLR_POSTS", self.POSTS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failures_use_real_sources_in_stable_order(self):
        expected = [f"artists:tumblr:{post}" for post in sorted(self.POSTS)]
        manifest = {"sources": [], "failures": []}
        with patch.object(artists.time, "sleep", lambda seconds: None), patch.object(artists.requests, "get", FakeWeb()):
            artists.collect_tumblr(manifest, [])
        self.assertEqual([row["id"] for row in manifest["sources"]], expected)
        self.assertTrue(all(row["status"] == "failed" for row in manifest["sources"]))
        self.assertEqual([(f["source_id"], f["url"]) for f in manifest["failures"]],
                         [(source_id, artists.TUMBLR_API) for source_id in expected])

        web = FakeWeb({artists.TUMBLR_API: "var tumblr_api_read = " + json.dumps({"posts": []}) + ";"})
        manifest = {"sources": [], "failures": []}
        with patch.object(artists.requests, "get", web):
            artists.collect_tumblr(manifest, [])
        self.assertEqual([f["source_id"] for f in manifest["failures"]], expected)


TUMBLR_POST = "129276170315"
TUMBLR_IMAGE = "https://64.media.tumblr.com/abc/tumblr_x_1280.png"


class ArtistRunTests(TempRoot):
    """Runs fetch_artists.main() with only the LIVE collector; the others find nothing."""

    LIVE = None

    def run_main(self, web, *flags):
        with contextlib.ExitStack() as stack:
            for name in {"collect_jimdo", "collect_tumblr", "collect_reference"} - {self.LIVE}:
                stack.enter_context(patch.object(artists, name, lambda *args, **kwargs: None))
            stack.enter_context(patch.object(artists, "ROOT", self.root))
            stack.enter_context(patch.object(artists, "PIXIV_POSTS", {}))
            stack.enter_context(patch.object(artists, "TUMBLR_POSTS", {TUMBLR_POST: "Mili Collection Vol. 2"}))
            stack.enter_context(patch.object(artists.time, "sleep", lambda seconds: None))
            stack.enter_context(patch.object(artists.requests, "get", web))
            stack.enter_context(patch.object(sys, "argv", ["fetch_artists.py", *flags]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return artists.main()

    def manifest(self):
        manifest = self.read_manifest("artists")
        manifest.pop("fetched_at")
        for asset in manifest["assets"]:
            self.assertEqual(sha((self.root / asset["path"]).read_bytes()), asset["sha256"], asset["id"])
        return manifest, {asset["id"]: asset for asset in manifest["assets"]}


class TumblrRefetchTests(ArtistRunTests):
    """A re-uploaded Tumblr image can keep its URL, so only the bytes tell its versions apart."""

    LIVE = "collect_tumblr"

    def test_unchanged_url_reupload_is_stable_and_a_revert_is_listed_once(self):
        post = {"id": TUMBLR_POST, "url-with-slug": f"https://wublaze.tumblr.com/post/{TUMBLR_POST}/x",
                "photos": [{"photo-url-1280": TUMBLR_IMAGE}]}
        red, blue = png("red"), png("blue")
        web = FakeWeb({artists.TUMBLR_API: "var tumblr_api_read = " + json.dumps({"posts": [post]}) + ";", TUMBLR_IMAGE: red})
        canonical = f"artists:tumblr:{TUMBLR_POST}:p0"
        stem = f"assets/public/artists/blaze-wu/tumblr-{TUMBLR_POST}-p00"
        self.assertEqual(self.run_main(web), 0)

        web.routes[TUMBLR_IMAGE] = blue
        self.assertEqual(self.run_main(web, "--refresh"), 0)
        refreshed, assets = self.manifest()
        self.assertEqual(sorted(assets), [canonical, f"{canonical}:{sha(red)[:12]}"])
        self.assertEqual((assets[canonical]["sha256"], assets[canonical]["path"]), (sha(blue), f"{stem}-{sha(blue)[:12]}.png"))
        self.assertEqual(assets[f"{canonical}:{sha(red)[:12]}"]["path"], f"{stem}.png")

        # Without --refresh the cached copy of the URL is the canonical record, never the superseded one.
        web.requested.clear()
        self.assertEqual(self.run_main(web), 0)
        self.assertNotIn(TUMBLR_IMAGE, web.requested)
        self.assertEqual(self.manifest()[0], refreshed)

        # Upstream reverts: the red version is canonical again and is not also kept as superseded.
        web.routes[TUMBLR_IMAGE] = red
        self.assertEqual(self.run_main(web, "--refresh"), 0)
        reverted, assets = self.manifest()
        self.assertEqual(sorted(assets), [canonical, f"{canonical}:{sha(blue)[:12]}"])
        self.assertEqual((assets[canonical]["sha256"], assets[canonical]["path"]), (sha(red), f"{stem}.png"))
        self.assertEqual(assets[f"{canonical}:{sha(blue)[:12]}"]["path"], f"{stem}-{sha(blue)[:12]}.png")
        self.assertEqual(self.run_main(web), 0)
        self.assertEqual(self.manifest()[0], reverted)


JIMDO_HTML = '<a data-href="{lightbox}"><img alt="{caption}" data-orig-width="{w}" data-orig-height="{h}"></a>'
LIGHTBOX = "https://image.jimcdn.com/app/cms/image/transf/dimension=2048x2048:format=png/path/s1/image/{key}/version/1/image.png"


class JimdoTests(TempRoot):
    def collect(self, images, previous=()):
        html = "".join(JIMDO_HTML.format(**image) for image in images)
        manifest, jobs = {"sources": [], "failures": []}, []
        with patch.object(artists.requests, "get", FakeWeb({artists.JIMDO_URL: html})):
            artists.collect_jimdo(manifest, jobs, previous)
        return manifest, jobs

    def image(self, title, key):
        return {"lightbox": LIGHTBOX.format(key=key), "caption": f"【Rayark様 音楽ゲーム「Deemo」】楽曲「{title}」アートワーク", "w": 4, "h": 4}

    def test_committed_records_keep_their_ids_and_file_names(self):
        committed = json.loads((ROOT / "data/sources/artists.json").read_text(encoding="utf-8"))
        records = [asset for asset in committed["assets"] if asset["source_id"] == "artists:jimdo:kolokolsan"]
        self.assertEqual(len(records), 5)
        images = [{"lightbox": r["advertised_lightbox_url"], "caption": r["source_caption"],
                   "w": r["source_dimensions"][0], "h": r["source_dimensions"][1]} for r in records]
        manifest, jobs = self.collect(images, committed["assets"])
        self.assertEqual(manifest["failures"], [])
        self.assertEqual(sorted((job["id"], job["stem"], job["download_url"]) for job in jobs),
                         sorted((r["id"], Path(r["path"]).stem, r["download_url"]) for r in records))

    def test_new_stems_are_unique_and_never_empty(self):
        manifest, jobs = self.collect([self.image("月光", "i0000000000000001"), self.image("花の言葉", "i0000000000000002"),
                                       self.image("Glaciology!", "i0000000000000003"), self.image("glaciology", "i0000000000000004")])
        self.assertEqual(manifest["failures"], [])
        self.assertEqual([job["stem"] for job in jobs],
                         ["jimdo-i0000000000000001", "jimdo-i0000000000000002",
                          "glaciology-i0000000000000003", "glaciology-i0000000000000004"])
        self.assertEqual(len({job["id"] for job in jobs}), 4)
        self.assertTrue(all(job["id"].endswith(":" + job["stem"]) for job in jobs))

    def test_duplicate_image_fails_fast(self):
        manifest, jobs = self.collect([self.image("A", "i0000000000000001"), self.image("B", "i0000000000000001")])
        self.assertEqual(jobs, [])
        self.assertEqual(manifest["sources"][0]["status"], "failed")
        self.assertIn("Duplicate", manifest["failures"][0]["error"])

    def test_lightbox_must_be_a_transform_url_on_the_jimdo_host(self):
        for lightbox in ("https://evil.example/image.jimcdn.com/app/cms/image/transf/none/image/i0000000000000001/x.png",
                         "https://evil.example\\@image.jimcdn.com/app/cms/image/transf/none/image/i0000000000000001/x.png",
                         "https://image.jimcdn.com/app/other/image/i0000000000000001/version/1/x.png"):
            image = {**self.image("A", "i0000000000000001"), "lightbox": lightbox}
            manifest, jobs = self.collect([image])
            self.assertEqual(jobs, [], lightbox)
            self.assertEqual(manifest["sources"][0]["status"], "failed")

    def test_duplicate_jobs_abort_before_downloading(self):
        job = {"id": "artists:x:1", "directory": "x", "stem": "one"}
        with self.assertRaises(SystemExit):
            artists.require_unique([job, dict(job)])
        with self.assertRaises(SystemExit):
            artists.require_unique([job, {**job, "id": "artists:x:2"}])


class JimdoRefetchTests(ArtistRunTests):
    """A Jimdo re-upload keeps its image id, so the canonical record keeps its asset id and file stem."""

    LIVE = "collect_jimdo"
    KEY = "i0000000000000001"

    def page(self, version):
        lightbox = LIGHTBOX.format(key=self.KEY).replace("/version/1/", f"/version/{version}/")
        html = JIMDO_HTML.format(lightbox=lightbox, caption="【Rayark様 音楽ゲーム「Deemo」】楽曲「Glaciology」アートワーク", w=4, h=4)
        return html, re.sub(r"/transf/[^/]+/", "/transf/none/", lightbox)

    def test_reupload_keeps_the_canonical_id_and_stem_on_a_cached_rerun(self):
        red, blue = png("red"), png("blue")
        html, first = self.page(1)
        web = FakeWeb({artists.JIMDO_URL: html, first: red})
        canonical = f"artists:jimdo:kolokolsan:glaciology-{self.KEY}"
        stem = f"assets/public/artists/kolokolsan/glaciology-{self.KEY}"
        self.assertEqual(self.run_main(web), 0)

        html, second = self.page(2)
        web.routes.update({artists.JIMDO_URL: html, second: blue})
        self.assertEqual(self.run_main(web), 0)
        reuploaded, assets = self.manifest()
        self.assertEqual(sorted(assets), [canonical, f"{canonical}:{sha(red)[:12]}"])
        self.assertEqual((assets[canonical]["sha256"], assets[canonical]["path"]), (sha(blue), f"{stem}-{sha(blue)[:12]}.png"))
        self.assertEqual(assets[f"{canonical}:{sha(red)[:12]}"]["path"], f"{stem}.png")

        # The image id maps to the canonical record, never to the superseded version listed after it.
        web.requested.clear()
        self.assertEqual(self.run_main(web), 0)
        self.assertEqual(web.requested, [artists.JIMDO_URL])
        self.assertEqual(self.manifest()[0], reuploaded)


# --------------------------------------------------------------- fetch_archives

CAA_API = f"https://coverartarchive.org/release/{archives.CAA_MBID}"


def caa_image(image_id):
    return {"id": image_id, "types": ["Front"], "approved": True,
            "image": f"http://coverartarchive.org/release/{archives.CAA_MBID}/{image_id}.jpg"}


class ArchivesTests(TempRoot):
    def setUp(self):
        super().setUp()
        patcher = patch.object(archives, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        archives.reset()
        self.addCleanup(archives.reset)

    def run_main(self, web, steps=None):
        with patch.object(archives, "session", lambda: web), patch.object(archives, "STEPS", archives.STEPS if steps is None else steps), \
                contextlib.redirect_stdout(io.StringIO()):
            return archives.main()

    def caa_step(self):
        return [step for step in archives.STEPS if step[1] == "cover-art-archive"]

    def caa_record(self, image_id, data):
        path = self.put(f"assets/public/archives/cover-art-archive/{image_id}.png", data)
        return {"id": f"archives:cover-art-archive:{image_id}", "source_id": "archives:cover-art-archive",
                "title": "scan", "kind": "reference", "page_url": "https://musicbrainz.org/x",
                "download_url": f"https://coverartarchive.org/release/{archives.CAA_MBID}/{image_id}.jpg",
                "path": path, "width": 4, "height": 4, "format": "PNG", "bytes": len(data), "sha256": sha(data),
                "fetched_at": "2026-01-01T00:00:00+00:00", "game": "DEEMO"}

    def test_offline_run_attributes_failures_and_exits_nonzero(self):
        old = png("red")
        self.write_manifest("archives", {"schema_version": 1, "sources": [
            {"id": "archives:cover-art-archive", "name": "CAA", "url": "https://musicbrainz.org/x", "status": "fetched", "notes": ""}],
            "assets": [self.caa_record(11, old), {**self.caa_record(12, png("green")), "upstream_status": "removed"}], "failures": []})
        self.assertEqual(self.run_main(FakeWeb()), 1)
        manifest = self.read_manifest("archives")
        sources = {row["id"]: row for row in manifest["sources"]}
        self.assertTrue(manifest["failures"])
        for failure in manifest["failures"]:
            self.assertIn(failure["source_id"], sources)
            self.assertTrue(failure["url"].startswith("https://"), failure)
        self.assertIn(("archives:cover-art-archive", CAA_API), [(f["source_id"], f["url"]) for f in manifest["failures"]])
        for sid in ("cover-art-archive", "rayarkmusic-tumblr", "kitsunefreak-cleaned", "official-deemo",
                    "deemo-exhibition", "rayark-brand-assets"):
            self.assertEqual(sources[f"archives:{sid}"]["status"], "failed", sid)
        self.assertEqual(sources["archives:steam-reborn"]["access_status"], "request_failed")
        self.assertEqual(manifest["failures"], sorted(manifest["failures"], key=lambda f: (f["source_id"], f.get("asset_id", ""), f["url"], f["error"])))
        kept = manifest["assets"]
        # A record already marked removed stays removed when its source fails.
        self.assertEqual([(a["id"], a["upstream_status"]) for a in kept],
                         [("archives:cover-art-archive:11", "fetch_failed"), ("archives:cover-art-archive:12", "removed")])
        self.assertEqual((self.root / kept[0]["path"]).read_bytes(), old)
        self.assertNotIn("retained unchanged", sources["archives:kitsunefreak-cleaned"]["notes"])

    def test_unexpected_step_error_is_filed_under_an_existing_source(self):
        class Broken:
            def get(self, url, **kwargs):
                raise RuntimeError(f"unexpected: {url}")  # not a RequestException, so check_entry lets it escape

        self.assertEqual(self.run_main(Broken()), 1)
        manifest = self.read_manifest("archives")
        sources = {row["id"] for row in manifest["sources"]}
        for failure in manifest["failures"]:
            self.assertIn(failure["source_id"], sources)
            self.assertTrue(failure["url"], failure)
        self.assertIn(("archives:internet-archive-202606", "https://archive.org/details/deemo_ost-_202606"),
                      [(f["source_id"], f["url"]) for f in manifest["failures"]])
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

    def test_partial_source_and_removed_record(self):
        web = FakeWeb({CAA_API: json.dumps({"images": [caa_image(11), caa_image(12)]}),
                       f"https://coverartarchive.org/release/{archives.CAA_MBID}/11.jpg": png("red")})
        self.assertEqual(self.run_main(web, self.caa_step()), 1)
        source = self.read_manifest("archives")["sources"][0]
        self.assertEqual((source["status"], source["assets_downloaded"]), ("partial", 1))
        failure = self.read_manifest("archives")["failures"][0]
        self.assertEqual(failure["asset_id"], "archives:cover-art-archive:12")

        old = png("green")
        self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": [self.caa_record(13, old)], "failures": []})
        web.routes[f"https://coverartarchive.org/release/{archives.CAA_MBID}/12.jpg"] = png("blue")
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        assets = {a["id"]: a for a in self.read_manifest("archives")["assets"]}
        self.assertEqual(assets["archives:cover-art-archive:13"]["upstream_status"], "removed")
        self.assertNotIn("upstream_status", assets["archives:cover-art-archive:11"])

    def test_record_of_an_unconfigured_source_keeps_its_source_row(self):
        retired = {"id": "archives:retired", "name": "Retired", "url": "https://deemo.com/old", "status": "fetched", "notes": ""}
        record = {**self.caa_record(11, png("red")), "id": "archives:retired:11", "source_id": retired["id"]}
        self.write_manifest("archives", {"schema_version": 1, "sources": [retired], "assets": [record], "failures": []})
        self.assertEqual(self.run_main(FakeWeb({CAA_API: json.dumps({"images": []})}), self.caa_step()), 0)
        manifest = self.read_manifest("archives")
        self.assertIn(retired, manifest["sources"])
        self.assertEqual([(a["id"], a["upstream_status"]) for a in manifest["assets"]], [(record["id"], "removed")])
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

    def test_lost_file_is_refetched_without_a_superseded_record(self):
        url = f"https://coverartarchive.org/release/{archives.CAA_MBID}/11.jpg"
        record = self.caa_record(11, png("red"))
        (self.root / record["path"]).unlink()  # the archived file is missing from the checkout
        self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": [record], "failures": []})
        web = FakeWeb({CAA_API: json.dumps({"images": [caa_image(11)]}), url: png("blue")})
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        self.assertEqual([(a["id"], a["path"], a["sha256"]) for a in self.read_manifest("archives")["assets"]],
                         [(record["id"], record["path"], sha(png("blue")))])
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

        # Green replaces blue, so blue is kept as superseded at 11.png. When 11.png is then lost and
        # upstream serves red, red takes that path and the blue record, whose bytes are gone, is dropped.
        web.routes[url] = png("green")
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        (self.root / record["path"]).unlink()
        web.routes[url] = png("red")
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        green = f"assets/public/archives/cover-art-archive/11-{sha(png('green'))[:12]}.png"
        self.assertEqual(sorted((a["id"], a["path"], a["sha256"]) for a in self.read_manifest("archives")["assets"]),
                         [(record["id"], record["path"], sha(png("red"))),
                          (f"{record['id']}:{sha(png('green'))[:12]}", green, sha(png("green")))])
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

        # 11.png is lost once more and upstream reverts to green, which is already stored beside it.
        # The canonical record moves onto that copy rather than a second one at 11.png, and the red
        # record stays listed as superseded: --verify reports its missing file until it is restored.
        (self.root / record["path"]).unlink()
        web.routes[url] = png("green")
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        assets = self.read_manifest("archives")["assets"]
        self.assertEqual(sorted((a["id"], a["path"], a.get("upstream_status")) for a in assets),
                         [(record["id"], green, None),
                          (f"{record['id']}:{sha(png('red'))[:12]}", record["path"], "superseded")])
        self.assertFalse((self.root / record["path"]).exists(), "a second copy of green was written")
        with self.assertRaises(FileNotFoundError), contextlib.redirect_stdout(io.StringIO()):
            archives.verify()
        self.put(record["path"], png("red"))  # restored from git
        self.assertEqual({a["path"] for a in assets}, self.archived_files(), "an archived file is no longer referenced")
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

    def test_lost_superseded_file_leaves_the_canonical_record_on_its_copy(self):
        url = f"https://coverartarchive.org/release/{archives.CAA_MBID}/11.jpg"
        record = self.caa_record(11, png("red"))
        self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": [record], "failures": []})
        web = FakeWeb({CAA_API: json.dumps({"images": [caa_image(11)]}), url: png("blue")})
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        before = self.read_manifest("archives")["assets"]
        self.assertEqual(sorted((a["id"], a["path"]) for a in before),
                         [(record["id"], f"assets/public/archives/cover-art-archive/11-{sha(png('blue'))[:12]}.png"),
                          (f"{record['id']}:{sha(png('red'))[:12]}", record["path"])])

        # The superseded red file is lost and upstream still serves blue: nothing moves, so no copy is
        # left without a record, and --verify reports the lost file until it is restored.
        (self.root / record["path"]).unlink()
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        self.assertEqual(self.read_manifest("archives")["assets"], before)
        self.assertFalse((self.root / record["path"]).exists(), "a second copy of blue was written")
        with self.assertRaises(FileNotFoundError), contextlib.redirect_stdout(io.StringIO()):
            archives.verify()
        self.put(record["path"], png("red"))  # restored from git
        self.assertEqual({a["path"] for a in before}, self.archived_files(), "an archived file is no longer referenced")
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()

    def test_reupload_supersedes_previous_version(self):
        old, new = png("red"), png("blue")
        self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": [self.caa_record(11, old)], "failures": []})
        web = FakeWeb({CAA_API: json.dumps({"images": [caa_image(11)]}),
                       f"https://coverartarchive.org/release/{archives.CAA_MBID}/11.jpg": new})
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        assets = {a["id"]: a for a in self.read_manifest("archives")["assets"]}
        canonical = assets["archives:cover-art-archive:11"]
        self.assertEqual(canonical["path"], f"assets/public/archives/cover-art-archive/11-{sha(new)[:12]}.png")
        superseded = assets[f"archives:cover-art-archive:11:{sha(old)[:12]}"]
        self.assertEqual((superseded["upstream_status"], superseded["superseded_by"], superseded["path"]),
                         ("superseded", "archives:cover-art-archive:11", "assets/public/archives/cover-art-archive/11.png"))
        self.assertEqual((self.root / superseded["path"]).read_bytes(), old)

        # Upstream reverts to the old bytes: they are canonical again and listed once.
        web.routes[f"https://coverartarchive.org/release/{archives.CAA_MBID}/11.jpg"] = old
        archives.reset()
        self.assertEqual(self.run_main(web, self.caa_step()), 0)
        assets = {a["id"]: a for a in self.read_manifest("archives")["assets"]}
        self.assertEqual(sorted(assets), ["archives:cover-art-archive:11", f"archives:cover-art-archive:11:{sha(new)[:12]}"])
        self.assertEqual((assets["archives:cover-art-archive:11"]["sha256"], assets["archives:cover-art-archive:11"]["path"]),
                         (sha(old), "assets/public/archives/cover-art-archive/11.png"))
        self.assertEqual(assets[f"archives:cover-art-archive:11:{sha(new)[:12]}"]["superseded_by"], "archives:cover-art-archive:11")

    def test_manifest_is_written_once_and_never_on_interrupt(self):
        path = self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": [], "failures": []})
        before = path.read_bytes()
        image = "https://deemo.com/a.png"
        web = FakeWeb({image: png("red")})

        def first():
            archives.source("x", "X", "https://deemo.com/", "fetched", "")
            archives.download("x", "a", "A", "https://deemo.com/", image, "illustration", hosts=archives.OFFICIAL_HOSTS)

        def second():
            archives.source("y", "Y", "https://deemo.com/", "fetched", "")

        def interrupted():
            raise KeyboardInterrupt

        # The first step completes and adds a source and an asset; the interrupt in the second leaves the manifest as it was.
        with self.assertRaises(KeyboardInterrupt):
            self.run_main(web, [(first, "x", "https://deemo.com/"), (interrupted, "y", "https://deemo.com/")])
        self.assertEqual(path.read_bytes(), before)
        archives.reset()
        writes = []
        original = archives.write_json
        with patch.object(archives, "write_json", lambda *args: (writes.append(args[0]), original(*args))):
            self.assertEqual(self.run_main(web, [(first, "x", "https://deemo.com/"), (second, "y", "https://deemo.com/")]), 0)
        self.assertEqual(writes, [path])
        self.assertEqual([asset["id"] for asset in self.read_manifest("archives")["assets"]], ["archives:x:a"])

        # A run killed before the rename leaves the complete previous manifest and no temporary file.
        current = path.read_bytes()
        with patch.object(archives.os, "replace", side_effect=OSError("killed")), self.assertRaises(OSError):
            archives.write_json(path, {"partial": True})
        self.assertEqual(path.read_bytes(), current)
        self.assertEqual(list(self.root.glob("data/sources/.*")), [])

    def test_verify_checks_failure_sources_and_superseded_by(self):
        record = self.caa_record(11, png("red"))
        source = {"id": "archives:cover-art-archive", "name": "CAA", "url": "https://musicbrainz.org/x", "status": "partial", "notes": ""}
        version = {**record, "id": record["id"] + ":0123456789ab", "upstream_status": "superseded", "superseded_by": record["id"]}
        good = {"schema_version": 1, "sources": [source], "assets": [record, version],
                "failures": [{"source_id": source["id"], "url": CAA_API, "error": "offline"}]}
        self.write_manifest("archives", good)
        with contextlib.redirect_stdout(io.StringIO()):
            archives.verify()
        for bad in ({**good, "failures": [{"source_id": "archives:catalog", "url": CAA_API, "error": "offline"}]},
                    {**good, "failures": [{"source_id": source["id"], "url": "", "error": "offline"}]},
                    {**good, "assets": [record, {**version, "superseded_by": "archives:cover-art-archive:12"}]}):
            self.write_manifest("archives", bad)
            with self.assertRaises(AssertionError), contextlib.redirect_stdout(io.StringIO()):
                archives.verify()

    def test_cover_filter_checks_hostnames(self):
        html = "".join(f'<img src="{src}">' for src in (
            "https://64.media.tumblr.com/tumblr_x_cover.jpg",
            "https://attacker.example/x?media.tumblr.com/_cover.png",
            "http://169.254.169.254/latest/media.tumblr.com/_cover.png",
            "https://media.tumblr.com.attacker.example/a_cover.png",
            "https://attacker.example\\@64.media.tumblr.com/tumblr_y_cover.png",
            "https://64.media.tumblr.com/tumblr_x_1280.jpg"))
        self.assertEqual(archives.post_covers(html, {}), ["https://64.media.tumblr.com/tumblr_x_cover.jpg"])

    def test_get_checks_every_redirect_hop(self):
        start = "https://coverartarchive.org/release/x/1.jpg"
        web = FakeWeb({start: (307, b"", {"Location": "https://archive.org/download/x/1.jpg"}),
                       # A relative Location is resolved against the hop that sent it.
                       "https://archive.org/download/x/1.jpg": (302, b"", {"Location": "/items/x/1.jpg"}),
                       "https://archive.org/items/x/1.jpg": (302, b"", {"Location": "https://ia800.us.archive.org/1.jpg"}),
                       "https://ia800.us.archive.org/1.jpg": png("red")})
        with patch.object(archives, "session", lambda: web):
            self.assertEqual(archives.get(start, archives.CAA_HOSTS).url, "https://ia800.us.archive.org/1.jpg")
            self.assertEqual(web.requested, [start, "https://archive.org/download/x/1.jpg",
                                             "https://archive.org/items/x/1.jpg", "https://ia800.us.archive.org/1.jpg"])
            for location in ("https://attacker.example/1.jpg", "http://archive.org/1.jpg", "http://169.254.169.254/",
                             "https://attacker.example\\@archive.org/1.jpg"):
                web.routes[start] = (302, b"", {"Location": location})
                web.requested.clear()
                with self.assertRaisesRegex(ValueError, "expected hosts"):
                    archives.get(start, archives.CAA_HOSTS)
                self.assertEqual(web.requested, [start])
            with self.assertRaisesRegex(ValueError, "expected hosts"):
                archives.get("https://evil.example/?coverartarchive.org", archives.CAA_HOSTS)

    def test_kitsunefreak_note_matches_the_result(self):
        url = "https://kitsunefreak.tumblr.com/api/read/json?tagged=my%20edit&num=50&start=0"
        empty = "var tumblr_api_read = " + json.dumps({"posts": [], "posts-total": 0}) + ";"
        with patch.object(archives, "session", lambda: FakeWeb({url: empty})):
            archives.tumblr_cleaned()
        row = archives.MANIFEST["sources"][0]
        self.assertNotIn("retained unchanged", row["notes"])
        self.assertIn("no images were retained", row["notes"])
        archives.reset()
        post = {"id": 5, "url": "https://kitsunefreak.tumblr.com/post/5", "tags": ["deemo"], "photo-caption": "Edit",
                "photo-url-1280": "https://64.media.tumblr.com/5_1280.png"}
        one = "var tumblr_api_read = " + json.dumps({"posts": [post], "posts-total": 1}) + ";"
        with patch.object(archives, "session", lambda: FakeWeb({url: one, post["photo-url-1280"]: png("red")})):
            archives.tumblr_cleaned()
        self.assertIn("retained unchanged", archives.MANIFEST["sources"][0]["notes"])


MIRROR = "https://rayarkmusic.tumblr.com"
MIRROR_API = f"{MIRROR}/api/read/json?tagged=Deemo%20Songs&num=50&start=0"
COVER_OK = "https://64.media.tumblr.com/a/tumblr_77_cover.png"
COVER_MOVED = "https://64.media.tumblr.com/b/tumblr_77b_cover.png"
IA_METADATA = "https://archive.org/metadata/deemo_ost-_202606"
EXHIBITION_PDF = "https://rayark.promo/deemo_exhibition_en.pdf"
BRAND_PDF = "https://rayark.promo/rayark_site/RAYARK_GameBrandAssets.pdf"


class ArchiveSiteTests(TempRoot):
    """tumblr_mirror, official and the Internet Archive lookup succeed offline, and off-host bait in
    the pages, the API data or a redirect is never requested."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(archives, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        archives.reset()
        self.addCleanup(archives.reset)

    def run_steps(self, web, *names):
        steps = [step for step in archives.STEPS if step[0].__name__ in names]
        self.assertEqual(len(steps), len(names))
        with patch.object(archives, "session", lambda: web), patch.object(archives, "STEPS", steps), \
                contextlib.redirect_stdout(io.StringIO()):
            code = archives.main()
            archives.verify()
        self.assertEqual([url for url in web.requested if "attacker.example" in url], [], "an off-host URL was requested")
        return code, self.read_manifest("archives")

    def web(self):
        posts = [{"id": "77", "url-with-slug": f"{MIRROR}/post/77/song", "id3-title": "Song", "tags": ["deemo"]},
                 {"id": "78", "url-with-slug": "https://attacker.example/post/78", "id3-title": "Bait"}]
        return FakeWeb({
            f"{MIRROR}/deemo": f'<a href="{MIRROR}/tagged/Deemo%20Songs">DEEMO</a><a href="{MIRROR}/tagged/Miscellaneous">misc</a>',
            MIRROR_API: "var tumblr_api_read = " + json.dumps({"posts": posts, "posts-total": 2}) + ";",
            # The off-host cover comes first, so letting it through would also renumber the real covers.
            f"{MIRROR}/post/77/song": f'<img src="https://attacker.example/tumblr_x_cover.png"><img src="{COVER_OK}"><img src="{COVER_MOVED}">',
            COVER_OK: png("red"),
            COVER_MOVED: (302, b"", {"Location": "https://attacker.example/tumblr_77b_cover.png"}),
            "https://deemo.com/": '<img src="/img/index_pic.png"><img src="https://attacker.example/about_pic.png"><img src="/img/logo.png">',
            "https://deemo.com/img/index_pic.png": (301, b"", {"Location": "https://www.deemo.com/img/index_pic.png"}),
            "https://www.deemo.com/img/index_pic.png": png("green"),
            EXHIBITION_PDF: b"%PDF-1.4 exhibition",
            BRAND_PDF: b"%PDF-1.4 brand assets",
            IA_METADATA: json.dumps({"files": [{"format": "Flac"}, {"format": "PNG"}, {"format": "PNG"}]}),
        })

    def test_pages_images_and_pdfs_come_only_from_their_hosts(self):
        code, manifest = self.run_steps(self.web(), "tumblr_mirror", "official", "catalog")
        self.assertEqual(code, 1)
        assets = {asset["id"]: asset for asset in manifest["assets"]}
        self.assertEqual(sorted(assets), ["archives:deemo-exhibition:deemo-exhibition", "archives:official-deemo:index_pic",
                                          "archives:rayark-brand-assets:rayark-brand-assets", "archives:rayarkmusic-tumblr:77-1"])
        self.assertEqual(assets["archives:rayarkmusic-tumblr:77-1"]["resolved_url"], COVER_OK)
        self.assertEqual(assets["archives:official-deemo:index_pic"]["resolved_url"], "https://www.deemo.com/img/index_pic.png")
        self.assertEqual(assets["archives:deemo-exhibition:deemo-exhibition"]["format"], "PDF")
        self.assertEqual(sorted((f["source_id"], f.get("asset_id", ""), f["url"]) for f in manifest["failures"]),
                         [("archives:official-deemo", "archives:official-deemo:about_pic", "https://attacker.example/about_pic.png"),
                          ("archives:rayarkmusic-tumblr", "", "https://attacker.example/post/78"),
                          ("archives:rayarkmusic-tumblr", "archives:rayarkmusic-tumblr:77-2", COVER_MOVED)])
        ia = next(row for row in manifest["sources"] if row["id"] == "archives:internet-archive-202606")
        self.assertEqual(ia["file_formats"], {"Flac": 1, "PNG": 2})

    def test_redirects_off_the_official_and_archive_hosts_are_refused(self):
        web = self.web()
        web.routes["https://deemo.com/"] = (302, b"", {"Location": "https://attacker.example/"})
        web.routes[IA_METADATA] = (302, b"", {"Location": "https://attacker.example/metadata"})
        code, manifest = self.run_steps(web, "official", "catalog")
        self.assertEqual(code, 1)
        failures = [(f["source_id"], f["url"]) for f in manifest["failures"]]
        self.assertIn(("archives:official-deemo", "https://deemo.com/"), failures)
        self.assertIn(("archives:internet-archive-202606", IA_METADATA), failures)
        # The reference PDFs do not depend on the website page.
        self.assertEqual(sorted(asset["id"] for asset in manifest["assets"]),
                         ["archives:deemo-exhibition:deemo-exhibition", "archives:rayark-brand-assets:rayark-brand-assets"])

    def test_a_final_server_error_fails_the_page_instead_of_reading_it_as_empty(self):
        # The session hands back the last 429/5xx after its retries, so get() must raise on it;
        # otherwise an empty error page would mark every archived record of the page "removed".
        web = self.web()
        previous = []
        for sid, key, page, color in (("official-deemo", "index_pic", "https://deemo.com/", "green"),
                                      ("rayarkmusic-tumblr", "77-1", f"{MIRROR}/deemo", "red")):
            web.routes[page] = (503, b"", {})
            data = png(color)
            previous.append({"id": f"archives:{sid}:{key}", "source_id": f"archives:{sid}", "title": key, "kind": "illustration",
                             "page_url": page, "download_url": f"{page}{key}.png", "path": self.put(f"assets/public/archives/{sid}/{key}.png", data),
                             "width": 4, "height": 4, "format": "PNG", "bytes": len(data), "sha256": sha(data),
                             "fetched_at": "2026-01-01T00:00:00+00:00", "game": "DEEMO"})
        self.write_manifest("archives", {"schema_version": 1, "sources": [], "assets": previous, "failures": []})
        code, manifest = self.run_steps(web, "tumblr_mirror", "official")
        self.assertEqual(code, 1)
        assets = {asset["id"]: asset for asset in manifest["assets"]}
        self.assertEqual([assets[row["id"]].get("upstream_status") for row in previous], ["fetch_failed", "fetch_failed"])
        self.assertEqual([(f["source_id"], f["url"]) for f in manifest["failures"]],
                         [("archives:official-deemo", "https://deemo.com/"), ("archives:rayarkmusic-tumblr", f"{MIRROR}/deemo")])
        self.assertTrue(all("503" in f["error"] for f in manifest["failures"]), manifest["failures"])
        status = {row["id"]: row["status"] for row in manifest["sources"]}
        self.assertEqual((status["archives:official-deemo"], status["archives:rayarkmusic-tumblr"]), ("failed", "failed"))


class ConnectionHostTests(unittest.TestCase):
    """The allow-list must judge the host requests really connects to, read here from the real HTTPAdapter."""

    HOSTS = ("media.tumblr.com",)
    REFUSED = ("https://attacker.example\\@64.media.tumblr.com/x_cover.png",
               "https://64.media.tumblr.com\\.attacker.example/x_cover.png",
               "https://64.media.tumblr.com@attacker.example/x_cover.png",
               "https://user:secret@64.media.tumblr.com/x_cover.png",
               "https://64.media.tumblr.com%2eattacker.example/x_cover.png",
               "https://64.media.tumblr.com:8443/x_cover.png",
               "https://xmedia.tumblr.com/x_cover.png",
               "https:///x_cover.png")
    ACCEPTED = ("https://64.media.tumblr.com/x_cover.png", "HTTPS://64.MEDIA.TUMBLR.COM./x_cover.png",
                "https://64.media.tumblr.com#@attacker.example/", "https://64.media.tumblr.com?@attacker.example/")

    def setUp(self):
        self.addCleanup(lambda: hasattr(archives.LOCAL, "session") and delattr(archives.LOCAL, "session"))

    def connection_hosts(self, call):
        hosts = []

        def send(adapter, request, **kwargs):
            hosts.append(adapter.build_connection_pool_key_attributes(request, True, None)[0]["host"])
            raise requests.ConnectionError("offline test")

        with patch.object(requests.adapters.HTTPAdapter, "send", send), contextlib.suppress(ValueError, requests.ConnectionError):
            call()
        return hosts

    def test_only_allowed_connection_hosts_are_contacted(self):
        fetches = {"archives.get": lambda url: archives.get(url, self.HOSTS),
                   "artists.get_within": lambda url: artists.get_within(url, {}, self.HOSTS)}
        for name, fetch in fetches.items():
            for module in (archives, artists):
                for url in self.REFUSED:
                    self.assertFalse(module.host_allowed(url, self.HOSTS), (module.__name__, url))
                for url in self.ACCEPTED:
                    self.assertTrue(module.host_allowed(url, self.HOSTS), (module.__name__, url))
            for url in self.REFUSED:
                self.assertEqual(self.connection_hosts(lambda: fetch(url)), [], (name, url))
            for url in self.ACCEPTED:
                hosts = self.connection_hosts(lambda: fetch(url))
                self.assertEqual([host.rstrip(".") for host in hosts], ["64.media.tumblr.com"], (name, url))

    def test_hosts_match_whole_labels_over_https_only(self):
        for module in (archives, artists):
            self.assertFalse(module.host_allowed("https://evilarchive.org/x", ("archive.org",)), module.__name__)
            self.assertTrue(module.host_allowed("https://ia800.us.archive.org/x", ("archive.org",)), module.__name__)
            self.assertFalse(module.host_allowed("http://i.pximg.net/img-original/1_p0.png", ("i.pximg.net",)), module.__name__)

    def test_only_the_scheme_is_upgraded_to_https(self):
        self.assertEqual(archives.https("http://deemo.com/a.png"), "https://deemo.com/a.png")
        for url in ("https://deemo.com/a?next=http://deemo.com/", "ftp://example.org/?http://deemo.com/"):
            self.assertEqual(archives.https(url), url)


class KeepFileTests(TempRoot):
    def test_different_bytes_are_never_written_over_an_archived_file(self):
        old, new = png("red"), png("blue")
        for module in (artists, archives):
            path = self.root / f"{module.__name__}.png"
            path.write_bytes(old)
            module.keep_file(path, old, sha(old))
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                module.keep_file(path, new, sha(new))
            self.assertEqual(path.read_bytes(), old)
            fresh = self.root / f"{module.__name__}-new.png"
            module.keep_file(fresh, new, sha(new))
            self.assertEqual(fresh.read_bytes(), new)


class ManifestWriteTests(TempRoot):
    def test_interrupted_manifest_write_keeps_the_previous_file(self):
        for module in (artists, archives):
            path = self.write_manifest(module.__name__, {"previous": True})
            before = path.read_bytes()
            with patch.object(module.os, "replace", side_effect=OSError("killed")), self.assertRaises(OSError):
                module.write_json(path, {"partial": True})
            self.assertEqual(path.read_bytes(), before, module.__name__)
            self.assertEqual(list(path.parent.glob(".*")), [], module.__name__)


class RetryStatusTests(unittest.TestCase):
    """The real HTTPAdapter/Retry stack against a loopback server: the final 429/5xx must reach the caller."""

    def setUp(self):
        self.hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                self.hits.append(handler.path)
                handler.send_response(int(handler.path.strip("/")))
                handler.send_header("Content-Length", "0")
                handler.end_headers()

            def log_message(handler, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        archives.reset()
        self.addCleanup(archives.reset)
        session = archives.session()
        session.trust_env = False
        session.mount("http://", session.get_adapter("https://example.invalid/"))
        self.addCleanup(lambda: delattr(archives.LOCAL, "session"))

    def check(self, status):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return archives.check_entry((f"t{status}", "Test", f"{base}/{status}", "index_only", ""))

    def test_rate_limit_is_blocked_and_server_error_is_http_error(self):
        row = self.check(429)
        self.assertEqual((row["access_status"], row["http_status"]), ("blocked", 429))
        self.assertEqual(len(self.hits), 3, "retries must still happen before giving up")
        row = self.check(503)
        self.assertEqual((row["access_status"], row["http_status"]), ("http_error", 503))
        self.assertEqual(self.check(403)["access_status"], "blocked")


# ----------------------------------------------------------- committed data, code

class CommittedDataTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "data/sources/archives.json").read_text(encoding="utf-8"))
        self.sources = {row["id"]: row for row in self.manifest["sources"]}

    def test_catalog_rows_match_the_script(self):
        for sid, name, url, status, notes in archives.CATALOG_ENTRIES:
            row = self.sources[f"archives:{sid}"]
            self.assertEqual((row["name"], row["url"], row["status"], row["notes"]), (name, url, status, notes))

    def test_notes_use_maintainer_wording(self):
        for row in self.manifest["sources"]:
            self.assertIsNone(re.search(r"\buser\b|workspace", row.get("notes", ""), re.I), row["id"])

    def test_illustrator_directory_is_delegated_to_the_wiki_importer(self):
        row = self.sources["archives:illustrator-directory"]
        self.assertEqual(row["status"], "delegated")
        self.assertIn("wikis:wikiwiki-illustrators", row["notes"])

    def test_kitsunefreak_note_does_not_claim_retained_images(self):
        row = self.sources["archives:kitsunefreak-cleaned"]
        self.assertEqual(row["assets_downloaded"], 0)
        self.assertEqual(row["notes"], archives.cleaned_note(row["posts_listed"], 0))
        self.assertNotIn("retained unchanged", row["notes"])

    def test_text_io_names_utf8(self):
        for name in ("fetch_artists", "fetch_archives"):
            tree = ast.parse((ROOT / f"scripts/{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("read_text", "write_text"):
                    self.assertIn("encoding", [keyword.arg for keyword in node.keywords], f"{name}.py:{node.lineno}")


if __name__ == "__main__":
    unittest.main()
