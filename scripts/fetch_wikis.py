#!/usr/bin/env python3
"""Fetch public DEEMO 1 wiki artwork without altering the downloaded images.

Dependencies: requests, Pillow. Run from anywhere; paths are relative to this repo.
The manifests preserve source metadata separately from measured downloaded bytes.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import quote

from PIL import Image
import requests


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "fandom": {
        "id": "wikis:fandom",
        "name": "DEEMO Wiki / Fandom (DEEMO 1 song categories)",
        "url": "https://deemo.fandom.com/wiki/Category:Songs",
        "api_url": "https://deemo.fandom.com/api.php",
        "page_base": "https://deemo.fandom.com/wiki/",
        "song_category": "Category:Songs",
    },
    "bwiki": {
        "id": "wikis:bwiki",
        "name": "Bilibili DEEMO Wiki",
        "url": "https://wiki.biligame.com/deemo/分类:歌曲",
        "api_url": "https://wiki.biligame.com/deemo/api.php",
        "page_base": "https://wiki.biligame.com/deemo/",
        "song_category": "Category:歌曲",
    },
}
HEADERS = {"User-Agent": "DEEMO-Public-Art-Archive/1.0 (personal research; 4 workers max)"}
# Transient statuses worth another attempt; 567 is BWIKI's EdgeOne rate limiting.
RETRY_STATUSES = {429, 500, 502, 503, 504, 567}
ATTEMPTS = 3
MAX_RETRY_AFTER = 30  # seconds; a longer Retry-After is capped so a run stays bounded
RASTER = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)
UI = re.compile(r"^(arrow|bbook|ac-icon|fc-icon|logo|wikilogo)(\.|$)|titletab|screenshot|^\d{8} |DEEMO[ _]II", re.I)
# Shortest edge of a collection cover; anything smaller is a UI tab or icon (the smallest real cover is 142 px).
MIN_COVER_EDGE = 100
# Characters MediaWiki forbids in page titles, so a link built from a name containing one cannot resolve.
ILLEGAL_TITLE = re.compile(r"[#<>\[\]{}|]")
# Optional record fields describing how a carried-forward record relates to the current upstream file.
UPSTREAM_FIELDS = ("upstream_status", "superseded_by")
# Optional candidate fields a record copies at its end, in this order (then delivery_note), on download and re-run.
SONG_INDEX_FIELDS = ("composer", "composer_source", "collection_aliases")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_delay(error, attempt):
    """Seconds to wait before the next attempt: the server's Retry-After (seconds or an HTTP date), capped at
    MAX_RETRY_AFTER, or else exponential backoff."""
    response = getattr(error, "response", None)
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                seconds = math.nan
        if not math.isnan(seconds):
            return min(max(seconds, 0.0), MAX_RETRY_AFTER)
    return min(2 ** attempt, 8)


def get(url, *, params=None, headers=None):
    for attempt in range(ATTEMPTS):
        try:
            response = requests.get(url, params=params, headers={**HEADERS, **(headers or {})}, timeout=(10, 25))
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            if isinstance(error, requests.HTTPError) and error.response.status_code not in RETRY_STATUSES:
                raise
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(retry_delay(error, attempt))


def api(source, **params):
    result = get(SOURCES[source]["api_url"], params={"action": "query", "format": "json", **params}).json()
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result


def category(source, title):
    rows = []
    continuation = {}
    while True:
        result = api(source, list="categorymembers", cmtitle=title, cmlimit="max", **continuation)
        rows.extend(row for row in result.get("query", {}).get("categorymembers", []) if row["ns"] == 0)
        if "continue" not in result:
            return rows
        continuation = result["continue"]


def allimages(source):
    rows = []
    continuation = {}
    while True:
        result = api(source, list="allimages", ailimit="max", aiprop="size|url|mime|timestamp|sha1", **continuation)
        rows.extend(result.get("query", {}).get("allimages", []))
        if "continue" not in result:
            return rows
        continuation = result["continue"]


def pages(source, titles):
    merged = {}
    for start in range(0, len(titles), 30):
        continuation = {}
        while True:
            result = api(source, titles="|".join(titles[start:start + 30]), redirects=1,
                         prop="revisions|images|categories|info", rvprop="content|timestamp", rvslots="main",
                         imlimit="max", cllimit="max", inprop="url", **continuation)
            for page in result.get("query", {}).get("pages", {}).values():
                if "missing" in page:
                    continue
                target = merged.setdefault(page["title"], {**page, "images": [], "categories": []})
                for field in ("images", "categories"):
                    seen = {row["title"] for row in target[field]}
                    target[field].extend(row for row in page.get(field, []) if row["title"] not in seen)
            if "continue" not in result:
                break
            continuation = result["continue"]
    return list(merged.values())


def imageinfo(source, titles):
    infos = {}
    for start in range(0, len(titles), 50):
        result = api(source, titles="|".join(titles[start:start + 50]), prop="imageinfo",
                     iiprop="size|url|mime|timestamp|sha1|extmetadata", iilimit=1)
        for page in result.get("query", {}).get("pages", {}).values():
            if page.get("imageinfo"):
                infos[page["title"]] = page["imageinfo"][0]
    return infos


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def unique_collections(names):
    """Sorted collection names, keeping one spelling of names that differ only in case, spacing or punctuation
    ("Etude Collection" / "Etude collection"); the first in code-point order wins, whatever the page or set order."""
    chosen = {}
    for name in sorted(set(names)):
        chosen.setdefault(normalized(name) or name, name)
    return sorted(chosen.values())


def with_collection_aliases(candidates, songs):
    """The candidates, each given the other spellings of its collections as collection_aliases.

    A record lists one spelling per collection, so a search for another spelling ("RAC collection -1" for
    "RAC Collection #1") would miss it. The aliases are every spelling that a song page of either wiki uses for one of
    the candidate's collections; the archive searches them, but does not show them."""
    spellings = defaultdict(set)
    for song in songs:
        for name in song["collections"]:
            spellings[normalized(name) or name].add(name)
    result = []
    for candidate in candidates:
        own = set(candidate["collections"])
        aliases = sorted({alias for name in own for alias in spellings.get(normalized(name) or name, ())} - own)
        rest = {key: value for key, value in candidate.items() if key != "collection_aliases"}
        result.append({**rest, "collection_aliases": aliases} if aliases else rest)
    return result


def with_composers(candidates, songs):
    """The candidates, each piece of song art given the composer of the songs it is mapped to.

    The join is on the exact song title: a candidate's song_titles are titles of song pages in the song index. The
    candidate's own wiki answers first, since its titles come from that wiki's pages; the other wiki's page of the same
    title answers only when none of the own wiki's pages names a composer (the wikis credit some songs differently).
    Several composers are joined with " / " in song-title order, and spellings that differ only in case, spacing or
    punctuation count once. A page's credit that holds several names apart only by a run of spaces (all the song index
    kept of a line break: "Jerry Barnes  Quiana") is split into those names, which HTML would otherwise run together
    into one. A composer taken from the other wiki also gets composer_source, that wiki's source id:
    otherwise the credit would read as the candidate's own wiki's. Collection covers get none: a cover is not one
    song's artwork."""
    by_title = defaultdict(list)
    for song in songs:
        if song.get("composer"):
            by_title[song["title"]].append(song)
    result = []
    for candidate in candidates:
        rest = {key: value for key, value in candidate.items() if key not in ("composer", "composer_source")}
        credits, own = [], SOURCES[candidate["source"]]["id"]
        if candidate.get("kind") == "song_art":
            matched = [song for title in candidate.get("song_titles", []) for song in by_title.get(title, ())]
            credits = [song for song in matched if song["source_id"] == own] or matched
        composers = {}
        for song in credits:
            for name in re.split(r"\s{2,}", song["composer"].strip()):
                composers.setdefault(normalized(name) or name, name)
        other = " / ".join(dict.fromkeys(song["source_id"] for song in credits if song["source_id"] != own))
        credit = {"composer": " / ".join(composers.values())} if composers else {}
        result.append({**rest, **credit, **({"composer_source": other} if other else {})})
    return result


def parameter(wikitext, name):
    match = re.search(r"\|\s*" + re.escape(name) + r"\s*=\s*([^|\n}]*)", wikitext, re.I)
    return re.sub(r"<!--.*?-->", "", match.group(1)).strip() if match else ""


def wikitext(page):
    revisions = page.get("revisions", [])
    return revisions[0].get("slots", {}).get("main", {}).get("*", "") if revisions else ""


def song_metadata(source, page):
    content = wikitext(page)
    collections = re.findall(r"\{\{Return\|([^|}\n]+)(?:\|([^}\n]+))?", content)
    row = {
        "title": page["title"], "page_url": page.get("fullurl", SOURCES[source]["page_base"] + quote(page["title"])),
        "source_id": SOURCES[source]["id"], "page_id": page["pageid"],
        "categories": [x["title"] for x in page.get("categories", [])],
        "image_files": [x["title"] for x in page.get("images", []) if RASTER.search(x["title"])],
    }
    # The Song template's Artist means composer, not illustrator.
    composer = parameter(content, "作曲家") if source == "bwiki" else parameter(content, "Artist")
    illustrator = parameter(content, "Illustrator") or parameter(content, "画师") or parameter(content, "曲绘作者")
    if composer:
        row["composer"] = composer
    if illustrator:
        row["illustrator"] = illustrator
    if source == "bwiki":
        row["collections"] = [parameter(content, "所属曲包")] if parameter(content, "所属曲包") else []
    else:
        row["collections"] = list(dict.fromkeys(display or target for target, display in collections))
    row["game"] = "DEEMO"
    return row


def discover_fandom():
    song_pages = pages("fandom", [x["title"] for x in category("fandom", "Category:Songs")])
    song_pages = [p for p in song_pages if not any("DEEMO II Songs" in c["title"] for c in p.get("categories", []))]
    songs = [song_metadata("fandom", page) for page in song_pages]
    candidates = {}
    # Merge in title order, so the lists and artist of an image shared by several pages do not depend on API order.
    for page, song in sorted(zip(song_pages, songs), key=lambda pair: pair[1]["title"]):
        content = wikitext(page)
        primary = normalized(parameter(content, "img") or page["title"])
        for image in page.get("images", []):
            filename = image["title"].split(":", 1)[1]
            if not RASTER.search(filename) or UI.search(filename):
                continue
            stem = filename.rsplit(".", 1)[0]
            kind = "collection_cover" if "booksprite" in filename.lower() else "song_art"
            entry = candidates.setdefault(image["title"], {"kind": kind, "song_titles": [], "related_pages": [], "collections": []})
            if song["title"] not in entry["song_titles"]:
                entry["song_titles"].append(song["title"])
            entry["related_pages"] = sorted(set(entry["related_pages"] + [song["page_url"]]))
            entry["collections"] = unique_collections(entry["collections"] + song["collections"])
            if normalized(stem) != primary:
                entry["variant_note"] = "Additional raster image referenced by the song page; may be alternate artwork or a composite."
            if song.get("illustrator"):
                entry["artist"] = song["illustrator"]
    collection_names = [x["title"] for x in category("fandom", "Category:Collections")]
    # Song Return templates also resolve older numbered collection aliases.
    for page in song_pages:
        collection_names.extend(re.findall(r"\{\{Return\|([^|}\n]+)", wikitext(page)))
    collection_pages = pages("fandom", sorted(set(collection_names)))
    covers = re.compile(r"booksprites?|^(collection \d+|deemo1[ab]|ad-piano [23]?|nmst2|djmax|egoist|vocal|team grimoire)\.", re.I)
    for page in collection_pages:
        if any("DEEMO II" in c["title"] for c in page.get("categories", [])):
            continue
        for image in page.get("images", []):
            filename = image["title"].split(":", 1)[1]
            if RASTER.search(filename) and not UI.search(filename) and covers.search(filename):
                entry = candidates.setdefault(image["title"], {"kind": "collection_cover", "song_titles": [], "related_pages": [], "collections": []})
                entry["related_pages"] = sorted(set(entry["related_pages"] + [page.get("fullurl", "")]))
    infos = imageinfo("fandom", sorted(candidates))
    selected = []
    excluded = []
    for title, candidate in sorted(candidates.items()):
        info = infos.get(title)
        if not info or not info.get("mime", "").startswith("image/"):
            excluded.append({"title": title, "reason": "No downloadable image metadata"})
            continue
        if candidate["kind"] == "song_art" and min(info["width"], info["height"]) < 300:
            excluded.append({"title": title, "reason": "Small UI/icon-sized image on song page", "width": info["width"], "height": info["height"]})
            continue
        if candidate["kind"] == "collection_cover" and min(info["width"], info["height"]) < MIN_COVER_EDGE:
            excluded.append({"title": title, "reason": "Icon-sized collection image", "width": info["width"], "height": info["height"]})
            continue
        selected.append({"source": "fandom", "file_title": title, "info": info, **candidate})
    return selected, songs, {"song_pages": len(songs), "collection_pages": len(collection_pages), "excluded": excluded}


def discover_bwiki(fandom_songs):
    song_pages = pages("bwiki", [x["title"] for x in category("bwiki", "Category:歌曲")])
    songs = [song_metadata("bwiki", page) for page in song_pages]
    lookup = defaultdict(list)
    for song in songs + fandom_songs:
        lookup[normalized(song["title"])].append(song)
    # A cover's related page is a BWIKI page, so BWIKI's own spelling of a collection wins; a Fandom spelling only
    # fills in a collection that no BWIKI song page names.
    collection_lookup = {}
    for group in (songs, fandom_songs):
        for name in unique_collections(collection for song in group for collection in song["collections"]):
            collection_lookup.setdefault(normalized(name), name)
    inventory = allimages("bwiki")
    selected = []
    excluded_large = []
    for info in inventory:
        if not info.get("mime", "").startswith("image/") or not RASTER.search(info["name"]):
            continue
        # The same UI exclusion as the Fandom path, applied to the page-title form (spaces, not underscores).
        if UI.search(info["name"].replace("_", " ")):
            continue
        stem = info["name"].rsplit(".", 1)[0].replace("_", " ")
        related = lookup.get(normalized(stem), [])
        if related and min(info["width"], info["height"]) >= 300:
            selected.append({"source": "bwiki", "file_title": info["title"], "info": info,
                             "kind": "song_art", "song_titles": sorted({s["title"] for s in related}),
                             "related_pages": sorted({s["page_url"] for s in related}),
                             "collections": unique_collections(c for s in related for c in s["collections"]),
                             "mapping_method": "Exact normalized song title / image filename match"})
        elif (normalized(stem) in collection_lookup or re.search(r"collection|selection|^Book of |^Epilogue$|^Shattered Memories", stem, re.I)) \
                and min(info["width"], info["height"]) >= MIN_COVER_EDGE:
            collection = collection_lookup.get(normalized(stem), stem)
            # A spelling that cannot be a title ("RAC collection #4") links to the file's own stem, which always can.
            page = stem if ILLEGAL_TITLE.search(collection) else collection
            selected.append({"source": "bwiki", "file_title": info["title"], "info": info,
                             "kind": "collection_cover", "song_titles": [], "collections": [collection],
                             "related_pages": [SOURCES["bwiki"]["page_base"] + quote(page)]})
        elif min(info["width"], info["height"]) >= 500:
            excluded_large.append({"title": info["title"], "width": info["width"], "height": info["height"],
                                   "reason": "No exact song/collection title mapping; not automatically identified as song artwork"})
    return selected, songs, {"song_pages": len(songs), "allimages_count": len(inventory), "excluded_large_images": excluded_large}


def asset_filename(title, digest, image_format):
    stem = title.split(":", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[^\w.-]+", "_", unicodedata.normalize("NFKC", stem), flags=re.UNICODE).strip("._")[:90] or "image"
    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}[image_format]
    return f"{stem}--{digest[:12]}.{extension}"


def delivery_note(url, size_matches, sha1_matches):
    """The caveat shown with a download, or None when the bytes are the plain original upload."""
    if "format=png" in url:
        if size_matches and sha1_matches:
            return ("Retrieved through the public full-size CDN PNG fallback (format=png); SHA-1 and size match the wiki "
                    "original upload. Downloaded bytes preserved unchanged.")
        return ("Retrieved through the public full-size CDN PNG fallback (format=png); may be CDN-reencoded, since the "
                "checksum/size differ from the wiki original upload. Downloaded bytes preserved unchanged.")
    if not size_matches or not sha1_matches:
        return ("Full-size public CDN file matches published dimensions but differs from the wiki original upload "
                "checksum/size; downloaded bytes preserved unchanged.")
    return None


def candidate_id(candidate):
    return "wikis:" + candidate["source"] + ":" + hashlib.sha256(candidate["file_title"].encode()).hexdigest()[:16]


def download(candidate, existing):
    source = candidate["source"]
    info = candidate["info"]
    asset_id = candidate_id(candidate)
    if asset_id in existing:
        previous = existing[asset_id]
        path = ROOT / previous["path"]
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == previous["sha256"] and previous.get("wiki_sha1") == info.get("sha1"):
            download_sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
            current = {key: value for key, value in previous.items()
                       if key not in UPSTREAM_FIELDS + SONG_INDEX_FIELDS + ("delivery_note",)}
            record = {**current, "kind": candidate["kind"], "song_titles": candidate["song_titles"],
                      "collections": candidate["collections"], "related_pages": candidate["related_pages"],
                      "download_sha1": download_sha1, "wiki_original_size_matches": path.stat().st_size == info["size"],
                      "wiki_original_sha1_matches": download_sha1 == info.get("sha1")}
            record.update({field: candidate[field] for field in SONG_INDEX_FIELDS if candidate.get(field)})
            note = delivery_note(previous.get("download_url", ""), record["wiki_original_size_matches"], record["wiki_original_sha1_matches"])
            if note:
                record["delivery_note"] = note
            return record
    url = info["url"]
    # Public Fandom CDN supports format=original, disabling implicit WebP negotiation.
    if source == "fandom":
        url = url.split("/revision/", 1)[0] + "?format=original"
    attempted_failures = []
    urls = [url]
    if source == "fandom":
        urls.append(info["url"] + ("&" if "?" in info["url"] else "?") + "format=original")
        if candidate.get("try_canonical_first"):
            urls.reverse()
        png_url = info["url"] + ("&" if "?" in info["url"] else "?") + "format=png"
        if candidate.get("try_png_first"):
            urls.insert(0, png_url)
        else:
            urls.append(png_url)
    for download_url in urls:
        try:
            response = get(download_url, headers={"Referer": quote(SOURCES[source]["url"], safe=":/")})
            url = download_url
            break
        except requests.RequestException as error:
            attempted_failures.append({"url": download_url, "error": str(error)})
    else:
        raise RuntimeError(json.dumps(attempted_failures))
    payload = response.content
    with Image.open(BytesIO(payload)) as image:
        image_format = image.format
        width, height = image.size
        image.verify()
    if image_format not in {"JPEG", "PNG", "WEBP", "GIF"}:
        raise ValueError(f"Unsupported actual image format: {image_format}")
    if (width, height) != (info["width"], info["height"]):
        raise ValueError(f"Unexpected dimensions: {(width, height)} != {(info['width'], info['height'])}")
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("assets/public/wikis") / source / asset_filename(candidate["file_title"], digest, image_format)
    (ROOT / relative).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / relative).write_bytes(payload)
    record = {
        "id": asset_id, "source_id": SOURCES[source]["id"],
        "title": candidate["file_title"].split(":", 1)[-1].rsplit(".", 1)[0],
        "kind": candidate["kind"], "page_url": info["descriptionurl"],
        "download_url": url, "resolved_download_url": response.url, "wiki_original_url": info["url"],
        "path": relative.as_posix(), "width": width, "height": height, "format": image_format,
        "bytes": len(payload), "sha256": digest, "fetched_at": now(), "game": "DEEMO",
        "content_type": response.headers.get("Content-Type"), "wiki_mime": info.get("mime"),
        "wiki_sha1": info.get("sha1"), "wiki_timestamp": info.get("timestamp"),
        "download_sha1": hashlib.sha1(payload).hexdigest(),
        "wiki_original_size_matches": len(payload) == info["size"],
        "wiki_original_sha1_matches": hashlib.sha1(payload).hexdigest() == info.get("sha1"),
        "song_titles": candidate["song_titles"], "collections": candidate["collections"],
        "related_pages": candidate["related_pages"], "provenance": "Public community wiki upload; original creator/upload lineage not independently established.",
    }
    for field in ("artist", "variant_note", "mapping_method"):
        if field in candidate:
            record[field] = candidate[field]
    if width == 2 * height or height == 2 * width:
        record["layout_note"] = "2:1 aspect ratio; may contain a texture atlas or combined artwork. Download preserved without cropping."
    if info.get("extmetadata"):
        record["wiki_extmetadata"] = info["extmetadata"]
    if attempted_failures:
        record["download_attempt_failures"] = attempted_failures
    # Placed as a verified re-run places them, so a later --resume keeps the key order.
    record.update({field: candidate[field] for field in SONG_INDEX_FIELDS if candidate.get(field)})
    note = delivery_note(url, record["wiki_original_size_matches"], record["wiki_original_sha1_matches"])
    if note:
        record["delivery_note"] = note
    return record


def failure_key(failure):
    return tuple(str(failure.get(field) or "") for field in ("source_id", "title", "url", "stage", "error"))


def merge_assets(existing, candidate_ids, results, failed_ids):
    """The manifest's records: this run's results merged over the previous records, so none is silently dropped.

    existing maps ids to the previous records, candidate_ids are the ids in the discovery snapshot, results maps ids to
    records verified or downloaded in this run, and failed_ids are candidates whose download failed. A previous record
    whose candidate left the snapshot stays as upstream_status "removed"; one whose re-download failed stays as
    "fetch_failed". When a re-uploaded file was downloaded, the new bytes keep the canonical id (deep links stay stable)
    and the previous version keeps its file and record under "<id>:<sha256[:12]>" as "superseded". Files are never
    deleted. Candidates without an outcome yet keep their previous record unchanged.
    """
    merged = {}
    for asset_id, previous in existing.items():
        record = results.get(asset_id)
        if record is not None:
            if record["sha256"] != previous["sha256"]:
                previous_id = f"{asset_id}:{previous['sha256'][:12]}"
                merged[previous_id] = {**previous, "id": previous_id, "upstream_status": "superseded", "superseded_by": asset_id}
        elif asset_id in failed_ids:
            merged[asset_id] = {**previous, "upstream_status": "fetch_failed"}
        elif asset_id in candidate_ids or previous.get("upstream_status") == "superseded":
            merged[asset_id] = previous
        else:
            merged[asset_id] = {**previous, "upstream_status": "removed"}
    merged.update(results)
    return [merged[asset_id] for asset_id in sorted(merged)]


def resumed_sources(sources, snapshot):
    """The previous manifest's sources, each with the discovery statistics of the snapshot being resumed when the
    snapshot recorded them; for an older snapshot without statistics the previous ones are kept."""
    stats = snapshot.get("stats", {})
    keys = {SOURCES[key]["id"]: key for key in stats if key in SOURCES}
    return [{**source, "discovery": {**stats[keys[source["id"]]], "snapshot_fetched_at": snapshot.get("fetched_at")}}
            if source["id"] in keys else source for source in sources]


def write_json(relative, data):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Write next to the target, then rename over it: an interrupted run never leaves a truncated file behind.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def fetch_song_keys():
    url = "https://raw.githubusercontent.com/syuchan1005/DeemoSongs/master/DeemoSongs.json"
    response = get(url)
    data = response.json()
    write_json("data/sources/song-mapping.json", {
        "schema_version": 1, "fetched_at": now(),
        "source_url": "https://github.com/syuchan1005/DeemoSongs", "download_url": url,
        "sha256": hashlib.sha256(response.content).hexdigest(), "data": data,
        "notes": "Public legacy game-key/song-title mapping; upstream data may predate later DEEMO releases. No art included.",
        "license": "MIT", "license_path": "licenses/DeemoSongs-MIT.txt",
    })


def fetch_illustrator_index(manifest):
    source = {"id": "wikis:wikiwiki-illustrators", "name": "Japanese DEEMO Wiki illustrator directory",
              "url": "https://wikiwiki.jp/deemo/アーティスト別リスト2", "status": "unavailable",
              "notes": "Reference directory for illustrator provenance; availability is checked without authentication or challenge bypass."}
    try:
        response = get(source["url"])
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        main = soup.select_one("#body") or soup.select_one("main") or soup
        rows = []
        for element in main.find_all(["h2", "h3", "h4", "tr"]):
            rows.append({"type": element.name, "text": element.get_text(" ", strip=True),
                         "links": [{"text": link.get_text(" ", strip=True), "url": link.get("href")} for link in element.find_all("a", href=True)]})
        write_json("data/sources/wiki-illustrator-index.json", {"schema_version": 1, "fetched_at": now(),
                   "source_url": source["url"], "rows": rows})
        source.update(status="complete", index_path="data/sources/wiki-illustrator-index.json")
    except Exception as error:
        manifest["failures"].append({"source_id": source["id"], "url": source["url"], "error": str(error), "stage": "reference_index"})
    manifest["sources"] = [s for s in manifest["sources"] if s["id"] != source["id"]] + [source]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true", help="Discover sources and metadata without downloading image bytes")
    parser.add_argument("--resume", action="store_true", help="Reuse the saved discovery snapshot and verify/retry its image downloads")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    args = parser.parse_args()
    manifest_path = ROOT / "data/sources/wikis.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    existing = {asset["id"]: asset for asset in previous.get("assets", [])}
    manifest = {"schema_version": 1, "fetched_at": now(), "sources": [], "assets": [], "failures": []}
    if args.resume:
        snapshot = json.loads((ROOT / "data/sources/wiki-discovery.json").read_text(encoding="utf-8"))
        candidates = snapshot["candidates"]
        for candidate in candidates:
            if "booksprite" in candidate["file_title"].lower():
                candidate["kind"] = "collection_cover"
        manifest["sources"] = resumed_sources(previous["sources"], snapshot)
        print(f"Resuming saved discovery: {len(candidates)} candidates", flush=True)
    else:
        print("Discovering Fandom original-DEEMO song pages...", flush=True)
        fandom, fandom_songs, fandom_stats = discover_fandom()
        print(f"Fandom: {len(fandom_songs)} song pages, {len(fandom)} image candidates", flush=True)
        print("Discovering BWIKI song pages and original allimages inventory...", flush=True)
        bwiki, bwiki_songs, bwiki_stats = discover_bwiki(fandom_songs)
        print(f"BWIKI: {len(bwiki_songs)} song pages, {len(bwiki)} image candidates", flush=True)
        stats, discovered_at = {"fandom": fandom_stats, "bwiki": bwiki_stats}, now()
        for source in ("fandom", "bwiki"):
            manifest["sources"].append({**SOURCES[source], "status": "discovered",
                                        "discovery": {**stats[source], "snapshot_fetched_at": discovered_at},
                                        "notes": "Only original DEEMO and its ports. DEEMO II categories, audio, charts, screenshots and unrelated UI are excluded."})
        songs = fandom_songs + bwiki_songs
        write_json("data/sources/wiki-song-index.json", {"schema_version": 1, "fetched_at": now(), "songs": songs})
        candidates = with_composers(with_collection_aliases(fandom + bwiki, songs), songs)
        # The statistics travel with the snapshot, so a later --resume can describe the enumeration it resumes.
        write_json("data/sources/wiki-discovery.json", {"schema_version": 1, "fetched_at": discovered_at, "stats": stats,
                                                         "candidates": candidates})
    if args.metadata_only:
        if not manifest_path.exists():
            write_json("data/sources/wikis.json", manifest)
        return
    if not args.resume:
        # The legacy song-key mapping is unrelated to the wiki candidates, so only a full run refreshes it.
        fetch_song_keys()
    previous_failures = {failure.get("title"): failure for failure in previous.get("failures", [])}
    for candidate in candidates:
        if candidate["file_title"] in previous_failures:
            candidate["try_canonical_first"] = True
            if str(previous_failures[candidate["file_title"]].get("error", "")).startswith("[{"):
                candidate["try_png_first"] = True
    candidate_ids = {candidate_id(item) for item in candidates}
    results, failed_ids = {}, set()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        jobs = {executor.submit(download, item, existing): item for item in candidates}
        for index, future in enumerate(as_completed(jobs), 1):
            item = jobs[future]
            try:
                record = future.result()
                results[record["id"]] = record
            except Exception as error:
                failed_ids.add(candidate_id(item))
                manifest["failures"].append({"source_id": SOURCES[item["source"]]["id"], "url": item["info"]["url"],
                                             "title": item["file_title"], "error": str(error)})
            if index % 25 == 0 or index == len(candidates):
                print(f"Downloaded/verified {index}/{len(candidates)}; failures={len(manifest['failures'])}", flush=True)
                # A checkpoint holds every previous record too, so an interrupted run never shrinks the manifest.
                manifest["assets"] = merge_assets(existing, candidate_ids, results, failed_ids)
                manifest["failures"].sort(key=failure_key)  # completion order varies between runs
                write_json("data/sources/wikis.json", manifest)
    manifest["assets"] = merge_assets(existing, candidate_ids, results, failed_ids)
    for source in manifest["sources"]:
        source["asset_count"] = sum(asset["source_id"] == source["id"] for asset in manifest["assets"])
        source["failure_count"] = sum(failure["source_id"] == source["id"] for failure in manifest["failures"])
        source["status"] = "partial" if source["failure_count"] else "complete"
    fetch_illustrator_index(manifest)
    manifest["fetched_at"] = now()
    manifest["assets"].sort(key=lambda row: row["id"])
    manifest["failures"].sort(key=failure_key)
    write_json("data/sources/wikis.json", manifest)
    print(json.dumps({"assets": len(manifest["assets"]), "failures": len(manifest["failures"]),
                      "bytes": sum(x["bytes"] for x in manifest["assets"]),
                      "kinds": dict(Counter(x["kind"] for x in manifest["assets"]))}), flush=True)


if __name__ == "__main__":
    main()
