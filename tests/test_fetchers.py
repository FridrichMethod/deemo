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

    def run_main(self, web):
        with patch.object(artists.requests, "get", web), patch.object(sys, "argv", ["fetch_artists.py", "--workers", "2"]), \
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
        for url in ("https://i.pximg.net.evil.example/1_p0.png", "https://169.254.169.254\\@i.pximg.net/img-original/1_p0.png"):
            web = FakeWeb()
            with patch.object(artists.requests, "get", web), self.assertRaisesRegex(ValueError, "expected hosts"):
                artists.download({**job, "download_url": url}, {}, False)
            self.assertEqual(web.requested, [])
        for location in ("http://169.254.169.254/latest", "https://169.254.169.254\\@i.pximg.net/1_p0.png"):
            web = FakeWeb({NEW_P0: (302, b"", {"Location": location})})
            with patch.object(artists.requests, "get", web), self.assertRaisesRegex(ValueError, "expected hosts"):
                artists.download({**job, "download_url": NEW_P0}, {}, False)
            self.assertEqual(web.requested, [NEW_P0])


class TumblrArtistTests(unittest.TestCase):
    def test_failures_use_real_sources_in_stable_order(self):
        expected = [f"artists:tumblr:{post}" for post in sorted(artists.TUMBLR_POSTS)]
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


class TumblrRefetchTests(TempRoot):
    """A re-uploaded Tumblr image can keep its URL, so only the bytes tell its versions apart."""

    def run_main(self, web, *flags):
        with patch.object(artists, "ROOT", self.root), patch.object(artists, "PIXIV_POSTS", {}), \
                patch.object(artists, "TUMBLR_POSTS", {TUMBLR_POST: "Mili Collection Vol. 2"}), \
                patch.object(artists, "collect_jimdo", lambda *args, **kwargs: None), \
                patch.object(artists, "collect_reference", lambda *args, **kwargs: None), \
                patch.object(artists.time, "sleep", lambda seconds: None), patch.object(artists.requests, "get", web), \
                patch.object(sys, "argv", ["fetch_artists.py", *flags]), contextlib.redirect_stdout(io.StringIO()):
            return artists.main()

    def manifest(self):
        manifest = self.read_manifest("artists")
        manifest.pop("fetched_at")
        for asset in manifest["assets"]:
            self.assertEqual(sha((self.root / asset["path"]).read_bytes()), asset["sha256"], asset["id"])
        return manifest, {asset["id"]: asset for asset in manifest["assets"]}

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

    def test_lightbox_host_is_checked_by_hostname(self):
        for lightbox in ("https://evil.example/image.jimcdn.com/app/cms/image/transf/none/image/i0000000000000001/x.png",
                         "https://evil.example\\@image.jimcdn.com/app/cms/image/transf/none/image/i0000000000000001/x.png"):
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
            "assets": [self.caa_record(11, old)], "failures": []})
        self.assertEqual(self.run_main(FakeWeb()), 1)
        manifest = self.read_manifest("archives")
        sources = {row["id"]: row for row in manifest["sources"]}
        self.assertTrue(manifest["failures"])
        for failure in manifest["failures"]:
            self.assertIn(failure["source_id"], sources)
            self.assertTrue(failure["url"].startswith("https://"), failure)
        for sid in ("cover-art-archive", "rayarkmusic-tumblr", "kitsunefreak-cleaned", "official-deemo",
                    "deemo-exhibition", "rayark-brand-assets"):
            self.assertEqual(sources[f"archives:{sid}"]["status"], "failed", sid)
        self.assertEqual(sources["archives:steam-reborn"]["access_status"], "request_failed")
        self.assertEqual(manifest["failures"], sorted(manifest["failures"], key=lambda f: (f["source_id"], f.get("asset_id", ""), f["url"], f["error"])))
        kept = manifest["assets"]
        self.assertEqual([(a["id"], a["upstream_status"]) for a in kept], [("archives:cover-art-archive:11", "fetch_failed")])
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

        def interrupted():
            archives.source("x", "X", "https://deemo.com/", "fetched", "")
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.run_main(FakeWeb(), [(interrupted, "x", "https://deemo.com/")])
        self.assertEqual(path.read_bytes(), before)
        archives.reset()
        writes = []
        original = archives.write_json
        with patch.object(archives, "write_json", lambda *args: (writes.append(args[0]), original(*args))):
            self.run_main(FakeWeb(), [])
        self.assertEqual(writes, [self.root / "data/sources/archives.json"])
        self.assertEqual(list(self.root.glob("data/sources/.*")), [])

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
                       "https://archive.org/download/x/1.jpg": (302, b"", {"Location": "https://ia800.us.archive.org/1.jpg"}),
                       "https://ia800.us.archive.org/1.jpg": png("red")})
        with patch.object(archives, "session", lambda: web):
            self.assertEqual(archives.get(start, archives.CAA_HOSTS).url, "https://ia800.us.archive.org/1.jpg")
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


class ConnectionHostTests(unittest.TestCase):
    """The allow-list must judge the host requests really connects to, read here from the real HTTPAdapter."""

    HOSTS = ("media.tumblr.com",)
    REFUSED = ("https://attacker.example\\@64.media.tumblr.com/x_cover.png",
               "https://64.media.tumblr.com\\.attacker.example/x_cover.png",
               "https://64.media.tumblr.com@attacker.example/x_cover.png",
               "https://user:secret@64.media.tumblr.com/x_cover.png",
               "https://64.media.tumblr.com%2eattacker.example/x_cover.png",
               "https://64.media.tumblr.com:8443/x_cover.png",
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
