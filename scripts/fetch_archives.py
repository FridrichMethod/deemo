#!/usr/bin/env python3
"""Fetch public DEEMO archival artwork without audio or image conversion.

Run from any directory: python -I scripts/fetch_archives.py
Dependencies: requests, beautifulsoup4, Pillow.
Publicly accessible does not imply redistribution permission.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import re
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "data/sources/archives.json"
STAMP = datetime.now(timezone.utc).isoformat()
LOCAL = threading.local()
MANIFEST = {"schema_version": 1, "fetched_at": STAMP, "sources": [], "assets": [], "failures": []}
LOCK = threading.Lock()
CAA_MBID = "fb7dec3c-01cd-4659-a398-e4494b824db4"


def reset():
    MANIFEST.update(fetched_at=STAMP, sources=[], assets=[], failures=[])


def session():
    if not hasattr(LOCAL, "session"):
        s = requests.Session()
        s.headers["User-Agent"] = "DEEMO-public-art-archive/1.0 (personal collection; public images only)"
        # raise_on_status=False: after the retries, hand back the last 429/5xx response instead of
        # raising RetryError, so callers can record the real status code.
        s.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.7, status_forcelist=[429, 500, 502, 503, 504],
                                                          raise_on_status=False)))
        LOCAL.session = s
    return LOCAL.session


def get(url):
    r = session().get(url.replace("http://", "https://", 1), timeout=(15, 60))
    r.raise_for_status()
    return r


def source(sid, name, url, status, notes, **extra):
    row = {"id": "archives:" + sid, "name": name, "url": url, "status": status, "notes": notes, **extra}
    MANIFEST["sources"].append(row)
    return row


def failure(sid, url, exc, **extra):
    with LOCK:
        MANIFEST["failures"].append({"source_id": "archives:" + sid, "url": url, "error": str(exc), "checked_at": STAMP, **extra})


def download(sid, key, title, page, url, kind, **extra):
    try:
        r = get(url)
        blob = r.content
        if blob.startswith(b"%PDF-"):
            fmt, width, height, ext = "PDF", None, None, ".pdf"
        else:
            with Image.open(io.BytesIO(blob)) as im:
                width, height, fmt = im.width, im.height, im.format
                im.verify()
            ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(fmt)
            if not ext:
                raise ValueError(f"Unsupported original format: {fmt}")
        digest = hashlib.sha256(blob).hexdigest()
        filename = re.sub(r"[^a-zA-Z0-9_-]", "-", str(key)).strip("-") + ext
        path = Path("assets/public/archives") / sid / filename
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            # Preserve the previous remote version if the source changes.
            path = path.with_name(path.stem + "-" + digest[:12] + ext)
            target = ROOT / path
        target.write_bytes(blob)
        row = {"id": f"archives:{sid}:{key}", "source_id": "archives:" + sid, "title": title,
               "kind": kind, "page_url": page, "download_url": url,
               "resolved_url": r.url, "path": path.as_posix(), "width": width,
               "height": height, "format": fmt, "bytes": len(blob), "sha256": digest,
               "fetched_at": STAMP, "game": "DEEMO", **extra}
        with LOCK:
            MANIFEST["assets"].append(row)
        return row
    except Exception as exc:
        failure(sid, url, exc, asset_id=f"archives:{sid}:{key}")
        return None


def pool(fn, items, workers=5):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(fn, items))


def cover_art_archive():
    sid = "cover-art-archive"
    page = f"https://musicbrainz.org/release/{CAA_MBID}/cover-art"
    record = source(sid, "DEEMO Song Collection — Cover Art Archive", page, "fetched",
                    "Community scans of official soundtrack packaging. Original API image files, not thumbnails; booklet spreads are references, not independent song masters.")
    api = f"https://coverartarchive.org/release/{CAA_MBID}"
    try:
        data = get(api).json()
    except Exception as exc:
        failure(sid, api, exc)
        return
    record["images_listed"] = len(data["images"])
    pool(lambda img: download(sid, img["id"], "DEEMO Song Collection — " + ", ".join(img["types"] or ["scan"]), page,
                              img["image"].replace("http://", "https://"), "reference",
                              provenance="community_scan", scan_types=img["types"], api_approved=img["approved"]), data["images"], 3)


def tumblr_api(blog, tag, start=0):
    url = f"https://{blog}.tumblr.com/api/read/json?tagged={quote(tag)}&num=50&start={start}"
    text = get(url).text
    if not text.startswith("var tumblr_api_read = "):
        raise ValueError(f"Tumblr public API unavailable at {url}")
    return json.loads(text.split(" = ", 1)[1].strip().rstrip(";"))


def tumblr_tag(blog, tag):
    start, posts = 0, []
    while True:
        data = tumblr_api(blog, tag, start)
        posts.extend(data["posts"])
        start += len(data["posts"])
        if not data["posts"] or start >= int(data["posts-total"]):
            return posts


def tumblr_mirror():
    sid, blog = "rayarkmusic-tumblr", "rayarkmusic"
    page = f"https://{blog}.tumblr.com/deemo"
    record = source(sid, "Tunes of Rayark — DEEMO", page, "fetched",
                    "Unofficial repost blog. Only image covers of DEEMO posts are fetched; no audio. FAQ describes cleaned artwork edits; these are not author masters.",
                    faq_url=f"https://{blog}.tumblr.com/faq", provenance="community_repost")
    try:
        doc = BeautifulSoup(get(page).text, "html.parser")
    except Exception as exc:
        failure(sid, page, exc)
        return
    tags = [unquote(a["href"].split("/tagged/", 1)[1]) for a in doc.find_all("a", href=True)
            if "/tagged/" in a["href"] and "Miscellaneous" not in a["href"]]
    def collect(tag):
        try:
            posts = tumblr_tag(blog, tag)
            return tag, posts
        except Exception as exc:
            failure(sid, f"https://{blog}.tumblr.com/tagged/{quote(tag)}", exc)
            return tag, []
    groups = pool(collect, tags, 4)
    record["tag_counts"] = {tag: len(posts) for tag, posts in groups}
    posts = {p["id"]: p for _, group in groups for p in group}
    record["posts_listed"] = len(posts)

    def fetch_post(post):
        post_url = post["url-with-slug"]
        try:
            soup = BeautifulSoup(get(post_url).text, "html.parser")
            covers = list(dict.fromkeys(img["src"] for img in soup.find_all("img", src=True)
                                        if "media.tumblr.com/" in img["src"] and "_cover." in img["src"]))
            # Some posts are standalone photos, already exposing largest API images.
            photos = post.get("photos", [])
            photos = photos or ([post] if post.get("photo-url-1280") else [])
            covers += [p.get("photo-url-1280") or p.get("photo-url-500") for p in photos]
            title = post.get("id3-title") or post.get("slug", post["id"])
            if not covers:
                failure(sid, post_url, "No public image cover found in post HTML/API")
            for n, url in enumerate(dict.fromkeys(covers), 1):
                if url:
                    download(sid, f"{post['id']}-{n}", title, post_url, url, "song_art",
                             provenance="community_repost", tags=post.get("tags", []),
                             quality_notes="May include typography, cleaned background, or reduced Tumblr audio-cover resolution.")
        except Exception as exc:
            failure(sid, post_url, exc)
    pool(fetch_post, posts.values(), 5)


def tumblr_cleaned():
    sid, blog = "kitsunefreak-cleaned", "kitsunefreak"
    page = f"https://{blog}.tumblr.com/tagged/my%20edit"
    record = source(sid, "Kitsunefreak cleaned DEEMO artwork", page, "fetched",
                    "Fan edits linked by the Tunes of Rayark FAQ. Public largest-size Tumblr images retained unchanged; source image was edited by uploader, not a production master.", provenance="community_edit")
    try:
        posts = tumblr_tag(blog, "my edit")
    except Exception as exc:
        failure(sid, page, exc)
        return
    posts = [p for p in posts if "deemo" in json.dumps(p.get("tags", [])).lower() or "deemo" in p.get("photo-caption", "").lower()]
    record["posts_listed"] = len(posts)
    for post in posts:
        photos = post.get("photos", []) or ([post] if post.get("photo-url-1280") else [])
        title = BeautifulSoup(post.get("photo-caption", ""), "html.parser").get_text(" ", strip=True)[:160] or post.get("slug", post["id"])
        for n, photo in enumerate(photos, 1):
            url = photo.get("photo-url-1280") or photo.get("photo-url-500")
            if url:
                download(sid, f"{post['id']}-{n}", title, post["url"], url, "illustration",
                         provenance="community_edit", tags=post.get("tags", []))


def official():
    sid, page = "official-deemo", "https://deemo.com/"
    source(sid, "DEEMO official website", page, "fetched", "Official website key art. Decorative illustrations; not a complete song-art library.",
           mirror_url="https://rayark.com/g/deemo/")
    try:
        soup = BeautifulSoup(get(page).text, "html.parser")
        names = {"index_pic.png", "about_pic.png", "screen_pic.png", "contact_pic.png"}
        images = {urljoin(page, img["src"]) for img in soup.find_all("img", src=True) if Path(urlparse(img["src"]).path).name in names}
        pool(lambda url: download(sid, Path(urlparse(url).path).stem, "DEEMO official website — " + Path(urlparse(url).path).stem,
                                  page, url, "illustration", rights_holder="Rayark / credited original artists", provenance="official_website"), images, 3)
    except Exception as exc:
        failure(sid, page, exc)
    # The reference PDFs do not depend on the website page, so they are fetched even when it fails.
    for key, title, url in [
        ("deemo-exhibition", "DEEMO Exhibition — official English guide", "https://rayark.promo/deemo_exhibition_en.pdf"),
        ("rayark-brand-assets", "Rayark Game Brand Assets", "https://rayark.promo/rayark_site/RAYARK_GameBrandAssets.pdf"),
    ]:
        source(key, title, url, "fetched", "Official reference PDF. Retained as reference; excluded from song-art gallery.")
        download(key, key, title, url, url, "reference", provenance="official_reference")


CATALOG_ENTRIES = [
    ("kadokawa-artbook", "DEEMO Visual Collection", "https://www.kadokawa.co.jp/product/322109001023/", "purchase_required", "Official 400+ artwork book; not downloaded because user excludes purchases."),
    ("bookwalker-artbook", "DEEMO Visual Collection — BookWalker", "https://bookwalker.jp/deebf58ee2-8a3a-4e18-bdc2-6f75f704dd7a/", "purchase_required", "Paid DRM ebook; no purchase, preview extraction, or DRM bypass attempted."),
    ("reddit-34", "DEEMO 3.4 extracted OST thread", "https://www.reddit.com/r/TrueDeemo/comments/c3dw62/", "index_only", "Historical soundtrack bundle; earlier source audit attributes artwork to Wikia. No audio downloaded."),
    ("reddit-4x", "DEEMO 4.x extracted OST thread", "https://www.reddit.com/r/TrueDeemo/comments/mzq53n/deemo_4x_extracted_ost/", "historical_unavailable", "Earlier search found reports of dead links; uploader mentions image scaling, so not native-resolution masters."),
    ("reddit-5x", "DEEMO 5.x extracted OST + Artwork thread", "https://www.reddit.com/r/TrueDeemo/comments/sgcr9r/deemo_5x_extracted_ost_artwork/", "historical_unavailable", "Earlier search found MEGA takedown reports. Uploader describes Wikia WebP converted to PNG. Not an independent master-art source."),
    ("reddit-art-database", "DEEMO in-game art database discussion", "https://www.reddit.com/r/TrueDeemo/comments/ssnzv6/", "index_only", "Discussion, not a verified downloadable higher-resolution archive."),
    ("original-extraction-blog", "Original repository extraction provenance", "https://2heng.xin/2018/04/05/python-pil/", "index_only", "Original repo author's explanation of Unity extraction and PIL processing. Attribution retained as provenance."),
    ("baidu-original", "Original extraction Baidu share", "https://pan.baidu.com/s/1HAux7DxzkJqcVysQ2hBCLA", "unverified_download", "Original repo-era Unity assets. Web share alone does not supply anonymous raw files; not evidence of higher-quality art."),
    ("baidu-tools", "Original extraction tools Baidu share", "https://pan.baidu.com/s/1eIyEgsc1WOJo6piwjgH-pQ", "out_of_scope", "Tools linked in extraction blog, not song-art masters."),
    ("dropbox-historical", "Historical Dropbox artwork share", "https://www.dropbox.com/sh/qdzxn3menwuk5he/AAAL2v6303lMlAf1ma54tJjga?dl=0", "historical_unavailable", "Checked shared-folder URL returns a Dropbox Error page despite HTTP 200; no public files available."),
    ("zerochan", "Zerochan DEEMO index", "https://www.zerochan.net/Deemo", "mixed_content_index", "Mixed official art, fanart, crops, and potential upscales; no verified new source singled out. Artist originals are handled separately."),
    ("safebooru", "Safebooru DEEMO tag", "https://safebooru.org/index.php?page=post&s=list&tags=deemo", "mixed_content_index", "Mixed third-party image board; no independently verified new official song art. Not batch imported as masters."),
    ("tumgik-blazewu", "Tumgik mirror of Blaze Wu", "https://www.tumgik.com/wublaze", "mirror_index", "Third-party mirror; fetch author Tumblr through the artist-source importer instead."),
    ("blazewu-tumblr", "Blaze Wu MILI Collection Vol.2", "https://wublaze.tumblr.com/post/129276170315/deemomili-collection-vol2songs-illustration", "delegated", "Author's original upload; handled by the separate artist-source importer to avoid duplicate ownership."),
    ("bilibili-video-search", "Bilibili DEEMO artwork videos", "https://search.bilibili.com/all?keyword=DEEMO%20%E6%9B%B2%E7%BB%98", "index_only", "Video search leads are recompressed frames, not loose native artwork; no videos/audio downloaded."),
    ("steam-reborn", "DEEMO -Reborn-", "https://store.steampowered.com/app/1282210/DEEMO_Reborn/", "purchase_required", "Paid game not available in this workspace; no game download. Shared original-game songs are only a subset."),
    ("steamdb-reborn", "DEEMO -Reborn- songcover bundle index", "https://steamdb.info/depot/1282212/", "index_only", "Lists Unity songcover bundles, not publicly downloadable image files. Actual texture dimensions remain unverified."),
    ("illustrator-directory", "Japanese DEEMO illustrator directory", "https://wikiwiki.jp/deemo/%E3%82%A2%E3%83%BC%E3%83%86%E3%82%A3%E3%82%B9%E3%83%88%E5%88%A5%E3%83%AA%E3%82%B9%E3%83%882", "index_only", "Artist-account routing directory; source artwork is fetched by the artist importer."),
]


def check_entry(entry):
    sid, name, url, status, notes = entry
    row = source(sid, name, url, status, notes)
    try:
        r = session().get(url, timeout=(12, 35))
        row.update(http_status=r.status_code, resolved_url=r.url, checked_at=STAMP)
        if r.status_code >= 400:
            row["access_status"] = "blocked" if r.status_code in (401, 403, 429) else "http_error"
        else:
            row["access_status"] = "page_reachable"
    except requests.RequestException as exc:
        row["access_status"] = "request_failed"
        row["access_error"] = str(exc)
    return row


def catalog():
    pool(check_entry, CATALOG_ENTRIES, 5)
    sid = "internet-archive-202606"
    row = source(sid, "Internet Archive DEEMO OST 202606", "https://archive.org/details/deemo_ost-_202606", "excluded_audio_derivatives",
                 "Metadata examined without downloading audio. PNG files derived from FLAC and spectrograms are audio visualizations, not original loose cover art. Audio metadata itself attributes embedded pictures to DEEMO Wiki.")
    metadata = "https://archive.org/metadata/deemo_ost-_202606"
    try:
        data = get(metadata).json()
        row["file_formats"] = dict(Counter(f.get("format", "unknown") for f in data.get("files", [])))
    except Exception as exc:
        failure(sid, metadata, exc)
    row["checked_at"] = STAMP


# Each step records its own network failures against the real source; the source id and URL here
# only label an unexpected error that escapes a step.
STEPS = (
    (cover_art_archive, "cover-art-archive", f"https://musicbrainz.org/release/{CAA_MBID}/cover-art"),
    (tumblr_mirror, "rayarkmusic-tumblr", "https://rayarkmusic.tumblr.com/deemo"),
    (tumblr_cleaned, "kitsunefreak-cleaned", "https://kitsunefreak.tumblr.com/tagged/my%20edit"),
    (official, "official-deemo", "https://deemo.com/"),
    (catalog, "internet-archive-202606", "https://archive.org/details/deemo_ost-_202606"),
)


def main():
    reset()
    for fn, sid, url in STEPS:
        print(f"Fetching {fn.__name__}", flush=True)
        try:
            fn()
        except Exception as exc:
            failure(sid, url, exc)
            print(f"FAILED {fn.__name__}: {exc}", flush=True)
    counts = Counter(a["source_id"] for a in MANIFEST["assets"])
    failed = Counter(f["source_id"] for f in MANIFEST["failures"])
    for row in MANIFEST["sources"]:
        row["assets_downloaded"] = counts[row["id"]]
        if row["status"] == "fetched":
            if failed[row["id"]]:
                row["status"] = "partial" if row["assets_downloaded"] else "failed"
            elif not row["assets_downloaded"]:
                row["status"] = "no_assets_fetched"
    # Written once, at the end: an interrupted run leaves the previous manifest untouched.
    save()
    print(json.dumps({"sources": len(MANIFEST["sources"]), "assets": len(MANIFEST["assets"]), "failures": len(MANIFEST["failures"]), "counts": counts}, indent=2), flush=True)
    return 1 if MANIFEST["failures"] else 0


def write_json(path, data):
    """Replace path through a temporary sibling, so a killed run never leaves truncated JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save():
    MANIFEST["sources"].sort(key=lambda row: row["id"])
    MANIFEST["assets"].sort(key=lambda row: row["id"])
    write_json(ROOT / MANIFEST_PATH, MANIFEST)


def verify():
    """Validate all recorded files without making network requests."""
    manifest = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    sources = {row["id"] for row in manifest["sources"]}
    assert len(sources) == len(manifest["sources"]), "Duplicate source ID"
    for row in manifest.get("failures", []):
        assert row["source_id"] in sources and row["url"], row
    ids = set()
    for asset in manifest["assets"]:
        assert asset["id"] not in ids, asset["id"]
        ids.add(asset["id"])
        assert asset["source_id"] in sources, asset["source_id"]
        path = (ROOT / asset["path"]).resolve()
        assert path.is_relative_to(ROOT), path
        blob = path.read_bytes()
        assert len(blob) == asset["bytes"], asset["path"]
        assert hashlib.sha256(blob).hexdigest() == asset["sha256"], asset["path"]
        if asset["format"] == "PDF":
            assert blob.startswith(b"%PDF-"), asset["path"]
        else:
            with Image.open(io.BytesIO(blob)) as image:
                assert (image.width, image.height, image.format) == (asset["width"], asset["height"], asset["format"]), asset["path"]
                image.verify()
    print(f"Verified {len(ids)} assets; checksums, bytes, dimensions, formats, IDs and source references valid.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--verify"]:
        verify()
    elif sys.argv[1:]:
        raise SystemExit("Usage: python -I scripts/fetch_archives.py [--verify]")
    else:
        raise SystemExit(main())
