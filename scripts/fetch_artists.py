#!/usr/bin/env python3
"""Archive publicly accessible DEEMO 1 artwork posted by its illustrators.

Requires requests, beautifulsoup4, and Pillow. No login credentials are used.
Images retain their downloaded bytes; Pillow only validates and reads metadata.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image
import requests


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "data/sources/artists.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DEEMOArtworkArchive/1.0)",
           "Accept": "*/*"}
PIXIV_POSTS = {
    "snowegg": {
        "artist": "雪歌草 / 雪蛋 / SnowEgg", "user_id": "1755315",
        "posts": {
            "87625394": "Sakuzyo Collection 2",
            "85567713": "Taishi Collection",
            "83464900": "Feryquitous Collection 2",
            "81179612": "AD:PIANO Collection 3",
            "67343822": "M2U × Nicode Collection",
            "56764190": "Sakuzyo Collection",
            "54831555": "Rabpit Collection",
        },
    },
    "ryori": {
        "artist": "Ryori", "user_id": "1400721",
        "posts": {"71679906": "AD:PIANO Collection 2",
                  "67258437": "Feryquitous Collection"},
    },
    "kai": {
        "artist": "K@I", "user_id": "93444",
        "posts": {"79134908": "DEEMO 3.6",
                  "75111180": "DEEMO 3.0 / Classical Collection",
                  "54551640": "Aioi Collection",
                  "51203078": "Book of Alice"},
    },
    "siyouko": {
        "artist": "Siyouko / 硝子", "user_id": "937764",
        "posts": {"47910628": "Deemoまとめ"},
    },
}
JIMDO_URL = "https://kolokolsan.jimdofree.com/仕事の制作物/"
TUMBLR_API = "https://wublaze.tumblr.com/api/read/json?tagged=deemo&num=50"
TUMBLR_POSTS = {
    "129276170315": "Mili Collection Vol. 2",
    "99918307835": "Eshen Chen Collection Vol. 1",
    "85916735720": "Mili Collection",
}
REFERENCE_URL = "https://princeofglass.blogspot.com/p/blog-page_1146.html"
# Image hosts per platform (an entry also admits its subdomains), checked by hostname on every
# download request and redirect hop.
DOWNLOAD_HOSTS = {"pixiv": ("i.pximg.net",), "jimdo": ("image.jimcdn.com",), "tumblr": ("media.tumblr.com",)}
MAX_REDIRECTS = 5


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def host_allowed(url, hosts):
    """True for an https URL whose hostname is one of hosts or a subdomain of one."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").rstrip(".")
    return (parts.scheme == "https" and port in (None, 443)
            and any(host == allowed or host.endswith("." + allowed) for allowed in hosts))


def get_within(url, headers, hosts):
    """GET url, following redirects only while every hop stays on https within hosts."""
    for _ in range(MAX_REDIRECTS + 1):
        if not host_allowed(url, hosts):
            raise ValueError(f"Refusing URL outside the expected hosts ({', '.join(hosts)}): {url}")
        response = requests.get(url, headers=headers, timeout=(15, 60), allow_redirects=False)
        if not response.is_redirect:
            return response
        url = urljoin(response.url, response.headers["location"])
    raise ValueError(f"Too many redirects: {url}")


def request(url, referer=None, hosts=None):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = requests.utils.requote_uri(referer)
    for attempt in range(3):
        try:
            if hosts:
                response = get_within(url, headers, hosts)
            else:
                response = requests.get(url, headers=headers, timeout=(15, 60))
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            if attempt == 2 or status in (401, 403, 404):
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Unreachable request retry state")


def pixiv_body(url):
    result = request(url, "https://www.pixiv.net/").json()
    if result.get("error"):
        raise ValueError(result.get("message", "Pixiv API error"))
    return result["body"]


def text_content(html):
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def record_failure(manifest, source_id, url, error, **extra):
    manifest["failures"].append({"source_id": source_id, "url": url,
                                 "error": str(error), **extra})


def failure_order(row):
    return row["source_id"], row.get("asset_id", ""), row["url"], row["error"]


def collect_pixiv(manifest, jobs):
    for directory, artist in PIXIV_POSTS.items():
        for post_id, collection in artist["posts"].items():
            page = f"https://www.pixiv.net/en/artworks/{post_id}"
            source_id = f"artists:pixiv:{post_id}"
            source = {"id": source_id, "name": collection, "url": page,
                      "artist": artist["artist"], "status": "pending",
                      "platform": "Pixiv", "access": "public_without_login"}
            manifest["sources"].append(source)
            try:
                metadata_url = f"https://www.pixiv.net/ajax/illust/{post_id}"
                metadata = pixiv_body(metadata_url)
                if str(metadata.get("userId")) != artist["user_id"]:
                    raise ValueError("Unexpected artwork owner")
                if metadata.get("xRestrict", 0) or metadata.get("isMasked", False):
                    raise ValueError("Restricted artwork is outside this public collection")
                pages_url = f"https://www.pixiv.net/ajax/illust/{post_id}/pages"
                pages = pixiv_body(pages_url)
                source.update({"name": metadata["illustTitle"],
                               "metadata_url": metadata_url,
                               "pages_metadata_url": pages_url,
                               "published_at": metadata.get("createDate"),
                               "updated_at": metadata.get("uploadDate"),
                               "artist_user_id": metadata["userId"],
                               "artist_display_name": metadata["userName"],
                               "description": text_content(metadata.get("illustComment")),
                               "tags": [t["tag"] for t in metadata.get("tags", {}).get("tags", [])],
                               "page_count": len(pages),
                               "notes": "Public original URLs returned by Pixiv; no authentication or transformation."})
                for index, entry in enumerate(pages):
                    if directory == "snowegg":
                        kind = "collection_cover" if index == 0 else "song_art"
                    elif directory == "ryori":
                        if index < 5:
                            kind = "song_art"
                        elif post_id == "71679906" and index in (5, 6):
                            kind = "collection_cover"
                        else:
                            kind = "reference"
                    elif directory == "kai":
                        kind = "song_art" if post_id == "79134908" else "contact_sheet"
                    else:
                        kind = "illustration"
                    jobs.append({"id": f"{source_id}:p{index}", "source_id": source_id,
                                 "artist": artist["artist"], "directory": directory,
                                 "stem": f"pixiv-{post_id}-p{index:02d}",
                                 "title": f"{collection} — page {index + 1}",
                                 "collection": collection, "collection_scope": "source_post_grouping",
                                 "kind": kind, "page_url": page,
                                 "download_url": entry["urls"]["original"],
                                 "source_page_index": index,
                                 "source_dimensions": [entry["width"], entry["height"]],
                                 "published_at": metadata.get("createDate"),
                                 "song_titles": [], "mapping_status": "unmapped",
                                 "quality": "artist_original_upload",
                                 "notes": ("Process/design reference; retained separately from song art."
                                           if kind == "reference" else
                                           "Artist's DEEMO illustration compilation; not asserted to be commissioned song artwork."
                                           if directory == "siyouko" else
                                           "Artist presentation includes decorative framing/copyright text; page order retained, exact song unmapped."
                                           if directory == "kai" else
                                           "Page order retained; no unverified song-title assignment. Source-post grouping may include covers for other collections.")})
                source["status"] = "discovered"
                print(f"Discovered {len(pages)} Pixiv images: {post_id}", flush=True)
            except Exception as error:
                source["status"] = "failed"
                record_failure(manifest, source_id, page, error)


def jimdo_image_id(url):
    """Jimdo's stable image id, the `/image/i<hex>/` segment of a CDN URL, or None."""
    match = re.search(r"/image/(i[0-9a-f]+)/", urlsplit(url or "").path)
    return match.group(1) if match else None


def collect_jimdo(manifest, jobs, previous_assets=()):
    source_id = "artists:jimdo:kolokolsan"
    source = {"id": source_id, "name": "ころころさん / 鳩紫ぽっぽ — 仕事の制作物",
              "url": JIMDO_URL, "status": "pending", "platform": "Jimdo",
              "artist": "ころころさん / 鳩紫ぽっぽ", "access": "public_without_login"}
    manifest["sources"].append(source)
    # Images already in the manifest keep their ids and file names (the first five used the bare
    # title slug); new ones are keyed by the Jimdo image id, prefixed with the slug when it is not empty.
    known = {jimdo_image_id(asset["download_url"]): asset["id"] for asset in previous_assets
             if asset["source_id"] == source_id}
    try:
        soup = BeautifulSoup(request(JIMDO_URL).text, "html.parser")
        images = [i for i in soup.find_all("img") if "「Deemo」" in i.get("alt", "")]
        if not images:
            raise ValueError("No explicitly labelled DEEMO images found")
        found, seen = [], set()
        for img in images:
            caption = img["alt"]
            title = re.search(r"楽曲「([^」]+)」", caption).group(1)
            lightbox = img.find_parent("a").get("data-href")
            key = jimdo_image_id(lightbox)
            if (not key or not host_allowed(lightbox, DOWNLOAD_HOSTS["jimdo"])
                    or not urlsplit(lightbox).path.startswith("/app/cms/image/transf/")):
                raise ValueError(f"Missing public lightbox URL for {title}")
            if key in seen:
                raise ValueError(f"Duplicate Jimdo image {key} for {title}")
            seen.add(key)
            asset_id = known.get(key) or f"{source_id}:{slug(title) or 'jimdo'}-{key}"
            original = re.sub(r"/transf/[^/]+/", "/transf/none/", lightbox)
            found.append({"id": asset_id, "source_id": source_id,
                         "artist": source["artist"], "directory": "kolokolsan",
                         "stem": asset_id.rsplit(":", 1)[1], "title": title, "song_titles": [title],
                         "kind": "song_art", "page_url": JIMDO_URL,
                         "download_url": original, "mapping_status": "source_caption",
                         "advertised_lightbox_url": lightbox,
                         "source_caption": caption,
                         "source_dimensions": [int(img["data-orig-width"]), int(img["data-orig-height"])],
                         "source_dimensions_kind": "advertised_lightbox_metadata; untransformed originals may be larger",
                         "quality": "artist_original_upload"})
        jobs.extend(found)
        source.update({"status": "discovered", "page_count": len(images),
                       "notes": "Only images explicitly labelled Deemo; public Jimdo transf/none URLs retain originals. The PNG originals are larger than the advertised 2048px lightbox metadata."})
    except Exception as error:
        source["status"] = "failed"
        record_failure(manifest, source_id, JIMDO_URL, error)


def collect_tumblr(manifest, jobs):
    try:
        response = request(TUMBLR_API).text
        data = json.loads(response.split("=", 1)[1].strip().rstrip(";"))
    except Exception as error:
        # Attribute the failure to every known post, so their archived records are kept as fetch_failed.
        for post_id, collection in sorted(TUMBLR_POSTS.items()):
            source_id = f"artists:tumblr:{post_id}"
            manifest["sources"].append({"id": source_id, "name": collection,
                                        "url": f"https://wublaze.tumblr.com/post/{post_id}", "status": "failed"})
            record_failure(manifest, source_id, TUMBLR_API, error)
        return
    seen = set()
    for post in data["posts"]:
        post_id = str(post["id"])
        if post_id not in TUMBLR_POSTS:
            continue
        seen.add(post_id)
        source_id = f"artists:tumblr:{post_id}"
        collection = TUMBLR_POSTS[post_id]
        page = post["url-with-slug"]
        source = {"id": source_id, "name": collection, "url": page,
                  "artist": "Blaze Wu", "platform": "Tumblr", "status": "discovered",
                  "access": "public_without_login", "metadata_url": TUMBLR_API,
                  "published_at": post.get("date-gmt"),
                  "description": text_content(post.get("photo-caption")),
                  "notes": "Largest image URL advertised by Tumblr's public legacy API; may be a platform-sized upload."}
        manifest["sources"].append(source)
        photos = post.get("photos") or [post]
        for index, photo in enumerate(photos):
            original = photo.get("photo-url-1280")
            if not original:
                record_failure(manifest, source_id, page, "No public large image URL", source_page_index=index)
                continue
            kind = "contact_sheet" if post_id != "129276170315" else ("collection_cover" if index == 5 else "song_art")
            jobs.append({"id": f"{source_id}:p{index}", "source_id": source_id,
                         "artist": "Blaze Wu", "directory": "blaze-wu",
                         "stem": f"tumblr-{post_id}-p{index:02d}",
                         "title": f"{collection} — page {index + 1}", "collection": collection,
                         "kind": kind, "page_url": page, "download_url": original,
                         "source_page_index": index, "published_at": post.get("date-gmt"),
                         "song_titles": [], "mapping_status": "unmapped",
                         "quality": "artist_public_platform_image"})
    for missing in sorted(TUMBLR_POSTS.keys() - seen):
        source_id = f"artists:tumblr:{missing}"
        page = f"https://wublaze.tumblr.com/post/{missing}"
        manifest["sources"].append({"id": source_id, "name": TUMBLR_POSTS[missing],
                                    "url": page, "status": "failed"})
        record_failure(manifest, source_id, page, "Known post not returned by public artist archive")


def collect_reference(manifest):
    source = {"id": "artists:blogspot:siyouko-career", "name": "Siyouko — career/commission record",
              "url": REFERENCE_URL, "artist": "Siyouko / 硝子", "status": "pending",
              "platform": "Blogspot", "access": "public_without_login"}
    manifest["sources"].append(source)
    try:
        soup = BeautifulSoup(request(REFERENCE_URL).text, "html.parser")
        body = soup.select_one(".post-body")
        if body is None or "DEEMO" not in body.get_text():
            raise ValueError("Expected DEEMO commission reference not found")
        source.update({"status": "reference_only", "notes": "Artist's career record confirms DEEMO cutscene, song artwork, and character-design work. This page contains no downloadable DEEMO artwork."})
    except Exception as error:
        source["status"] = "failed"
        record_failure(manifest, source["id"], REFERENCE_URL, error)


def download(job, cache, refresh):
    previous = cache.get(job["download_url"])
    if previous and not refresh:
        path = ROOT / previous["path"]
        if path.is_file():
            data = path.read_bytes()
            if sha256(data).hexdigest() == previous["sha256"]:
                return job, data, previous["fetched_at"]
    platform = job["source_id"].split(":")[1]
    response = request(job["download_url"], job["page_url"], DOWNLOAD_HOSTS[platform])
    return job, response.content, now()


def replace_file(path, data):
    """Write bytes through a temporary sibling and os.replace, so no partial file is ever left at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def keep_file(path, data, digest):
    """Store data at path; an identical file is left alone and different bytes are never overwritten."""
    if path.exists():
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Refusing to overwrite different bytes at {path}")
        return
    replace_file(path, data)


def write_json(path, data):
    replace_file(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def save_asset(job, data, fetched_at, hashes):
    with Image.open(BytesIO(data)) as img:
        width, height, fmt, mode = img.width, img.height, img.format, img.mode
        img.verify()
    with Image.open(BytesIO(data)) as img:
        img.load()
    digest = sha256(data).hexdigest()
    extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}.get(fmt)
    if extension is None:
        raise ValueError(f"Unexpected image format: {fmt}")
    relative = f"assets/public/artists/{job['directory']}/{job['stem']}{extension}"
    duplicate_of = None
    if digest in hashes:
        relative, duplicate_of = hashes[digest]
    else:
        path = ROOT / relative
        if path.exists() and sha256(path.read_bytes()).hexdigest() != digest:
            # Upstream bytes changed: keep the archived file and store the new version beside it.
            relative = f"assets/public/artists/{job['directory']}/{job['stem']}-{digest[:12]}{extension}"
            path = ROOT / relative
        keep_file(path, data, digest)
        hashes[digest] = (relative, job["id"])
    asset = {key: value for key, value in job.items() if key not in ("directory", "stem")}
    asset.update({"path": relative, "width": width, "height": height, "format": fmt,
                  "mode": mode, "bytes": len(data), "sha256": digest, "fetched_at": fetched_at,
                  "game": "DEEMO", "rights": "Artwork remains copyright of Rayark and/or the original artist; no redistribution license inferred.",
                  "transformed": False})
    if duplicate_of:
        asset["duplicate_of"] = duplicate_of
    return asset


def fetch_all(manifest, jobs, cache, refresh, workers):
    """Download in parallel but save in asset-id order, so the SHA-256 dedup primary is deterministic."""
    hashes = {}
    ordered = sorted(jobs, key=lambda job: job["id"])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, job, cache, refresh) for job in ordered]
        for job, future in zip(ordered, futures):
            try:
                result = save_asset(*future.result(), hashes)
                manifest["assets"].append(result)
                print(f"Saved {result['id']} {result['width']}x{result['height']}", flush=True)
            except Exception as error:
                record_failure(manifest, job["source_id"], job["download_url"], error, asset_id=job["id"])
                print(f"Failed {job['id']}: {error}", flush=True)
    return hashes


def require_unique(jobs):
    """Stop before any download when two jobs would share an asset id or a file stem."""
    for label, keys in (("asset id", [job["id"] for job in jobs]),
                        ("file stem", [f"{job['directory']}/{job['stem']}" for job in jobs])):
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        if duplicates:
            raise SystemExit(f"Duplicate {label}: {', '.join(duplicates)}")


def carry_forward(previous, manifest):
    """Merge the previous manifest into this run's result so no verified record is dropped.

    A previous record this run did not reproduce stays, with upstream_status "removed" when its
    source was read without error and no longer lists it, or "fetch_failed" when the record or its
    source failed this run. When upstream bytes changed, the new version keeps the canonical id and
    the old one stays as "<id>@<old sha256[:12]>" with upstream_status "superseded" and superseded_by
    naming the canonical id. Files are never deleted; sources of kept records are kept too.
    """
    records = {row["id"]: row for row in manifest["assets"]}
    failed_assets = {row["asset_id"] for row in manifest["failures"] if row.get("asset_id")}
    failed_sources = {row["source_id"] for row in manifest["failures"]}
    for old in previous.get("assets", []):
        new = records.get(old["id"])
        if new is not None:
            if new["sha256"] != old["sha256"]:
                version = {**old, "id": f"{old['id']}@{old['sha256'][:12]}",
                           "upstream_status": "superseded", "superseded_by": old["id"]}
                records.setdefault(version["id"], version)
        elif old.get("upstream_status") == "superseded":
            records.setdefault(old["id"], old)
        elif old["id"] in failed_assets or old["source_id"] in failed_sources:
            status = "removed" if old.get("upstream_status") == "removed" else "fetch_failed"
            records[old["id"]] = {**old, "upstream_status": status}
        else:
            records[old["id"]] = {**old, "upstream_status": "removed"}
    manifest["assets"] = list(records.values())
    missing = {row["source_id"] for row in manifest["assets"]} - {row["id"] for row in manifest["sources"]}
    manifest["sources"].extend(row for row in previous.get("sources", []) if row["id"] in missing)


def keep_source_details(previous, manifest):
    """A source whose discovery failed keeps the metadata of its previous successful read."""
    old = {row["id"]: row for row in previous.get("sources", [])}
    manifest["sources"] = [{**row, **old[row["id"]], "status": "failed"}
                           if row["status"] == "failed" and row["id"] in old else row
                           for row in manifest["sources"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Redownload even when cached file hashes match")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    manifest_path = ROOT / MANIFEST
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    # Superseded versions stay in the manifest but are no longer the cached copy of their URL.
    current = [asset for asset in previous.get("assets", []) if asset.get("upstream_status") != "superseded"]
    cache = {asset["download_url"]: asset for asset in current}
    manifest = {"schema_version": 1, "fetched_at": now(), "sources": [], "assets": [], "failures": [],
                "scope": "DEEMO 1 artist-published illustrations, original public uploads and public platform images",
                "excluded": "Paid artbooks; DEEMO II; unrelated games, unrelated fanart and cross-game anniversary posts; credentials and login walls"}
    jobs = []
    collect_pixiv(manifest, jobs)
    collect_jimdo(manifest, jobs, current)
    collect_tumblr(manifest, jobs)
    collect_reference(manifest)
    keep_source_details(previous, manifest)
    require_unique(jobs)
    hashes = fetch_all(manifest, jobs, cache, args.refresh, args.workers)
    successes = Counter(asset["source_id"] for asset in manifest["assets"])
    failures = Counter(failure["source_id"] for failure in manifest["failures"])
    for source in manifest["sources"]:
        if source["status"] == "discovered":
            source["status"] = "partial" if failures[source["id"]] else "complete"
            if successes[source["id"]] == 0:
                source["status"] = "failed"
    carry_forward(previous, manifest)
    records = Counter(asset["source_id"] for asset in manifest["assets"])
    for source in manifest["sources"]:
        source["asset_count"] = records[source["id"]]
    manifest["assets"].sort(key=lambda asset: asset["id"])
    manifest["sources"].sort(key=lambda source: source["id"])
    manifest["failures"].sort(key=failure_order)
    write_json(manifest_path, manifest)
    print(json.dumps({"sources": len(manifest["sources"]), "assets": len(manifest["assets"]),
                      "unique_files": len(hashes), "failures": len(manifest["failures"])}, indent=2))
    return 1 if manifest["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
