"""Build the catalog and slideshow without modifying any image bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFESTS = ("artists", "wikis", "archives")
# Legacy keys that name a collection cover: the book sprites, and deemo1a/deemo1b, the Deemo's Collection Vol.1A/1B
# covers (the same files are covers on Fandom; see the cover pattern in scripts/fetch_wikis.py).
COVER_KEY = re.compile(r"booksprite|bookcover|^deemo1[ab]$", re.IGNORECASE)
# Optional fields on a record the fetchers carried forward rather than dropped: its upstream file was removed, was
# superseded by a newer upload (superseded_by names the record with the canonical id), or failed to re-download.
UPSTREAM_STATUSES = ("removed", "superseded", "fetch_failed")
# Every tracked file here must be a record's or a variant's path (assets/thumbs/ has its own check).
ORPHAN_CHECK_DIRECTORIES = ("assets/public", "assets/legacy")


def attr(value: object) -> str:
    """A double- or single-quoted attribute value. CR, LF and TAB become character references, so HTML parsing
    (which turns a raw CR into LF) and --check (which compares the file exactly) both keep the value as it is."""
    return escape(str(value), {'"': "&quot;", "'": "&#39;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"})


def safe_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Unsafe asset path: {value}")
    if relative.parts[0] != "assets":
        raise ValueError(f"Unexpected asset directory: {value}")
    # Resolving follows symlinks: a linked file or directory must still land inside the repository's assets/.
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_relative_to((root / "assets").resolve()):
        raise ValueError(f"Asset outside the repository's assets directory: {value}")
    return path


def tracked_files(root: Path, directories: tuple[str, ...]) -> list[str] | None:
    """Git-tracked files under these directories, as sorted POSIX paths relative to root, skipping dot-files and
    __pycache__ as scripts/prepare_pages.py does. None when root is not the top of a git checkout."""
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        if Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *directories], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        if (root / ".git").exists():
            print(f"warning: cannot list tracked files, so unreferenced assets are not checked: {error}", file=sys.stderr)
        return None
    names = (name.decode("utf-8") for name in listed.stdout.split(b"\0") if name)
    return sorted(name for name in names if not any(part.startswith(".") or part == "__pycache__" for part in name.split("/")))


def required_input(root: Path, relative: str, directory: bool = False) -> Path:
    """An input every build needs: a missing one fails loudly rather than quietly building a smaller site."""
    path = root / relative
    if not (path.is_dir() if directory else path.is_file()):
        raise FileNotFoundError(f"Missing required input: {relative}")
    return path


def fold(text: str) -> str:
    """A name with case, spacing and punctuation dropped, for telling spellings of one name apart from other names."""
    return "".join(char for char in text.casefold() if char.isalnum())


def wiki_file_key_songs(records: list[dict]) -> dict[str, tuple[list[str], str]]:
    """The songs wiki uploads of song artwork map their file keys to: folded file key ("Samsara105 fc" ->
    "samsara105fc") -> (song titles, the first such upload's page), for keys whose uploads all agree on the songs."""
    found: dict[str, tuple[list[str], str]] = {}
    disputed = set()
    for record in sorted(records, key=lambda record: record["id"]):
        key = fold(str(record.get("title") or ""))
        if record.get("kind") != "song_art" or not record.get("song_titles") or not key:
            continue
        if key in found and found[key][0] != list(record["song_titles"]):
            disputed.add(key)
        found.setdefault(key, (list(record["song_titles"]), record["page_url"]))
    return {key: value for key, value in found.items() if key not in disputed}


def legacy_pngs(directory: Path) -> list[Path]:
    """A legacy directory's PNGs in file-name order. The explicit key and the exact suffix test give the same list
    on every platform (WindowsPath sorts case-folded, and glob matches case-insensitively on Windows)."""
    return sorted((path for path in directory.iterdir() if path.suffix == ".png"), key=lambda path: path.name)


def mapped_song(songs: dict, key: str) -> tuple[dict, str]:
    """The song-mapping entry for a legacy texture key, with its title_status. An exact key wins; failing that, the
    one mapping key that differs only in case ("Randall" -> "randall"). Several such keys leave the key unmapped."""
    if songs.get(key):
        return songs[key], "mapped_exact_internal_key"
    variants = [name for name in songs if name != key and name.casefold() == key.casefold()]
    if len(variants) == 1 and songs[variants[0]]:
        return songs[variants[0]], "mapped_case_insensitive_internal_key"
    return {}, "internal_key"


def legacy_assets(root: Path, wiki_records: list[dict] | None = None) -> list[dict]:
    """The inherited textures, each mapped to its song by its internal key in the song mapping (mapped_song()). A key
    the mapping lacks takes the songs of the wiki uploads of song artwork whose file has the same key, ignoring case,
    spacing and punctuation ("Samsara105 fc" for samsara105_fc), when they agree; it is marked mapped_wiki_file_key and
    its mapping_source is that upload's page. Otherwise the same texture would be unmapped here and song artwork there."""
    wiki_songs = wiki_file_key_songs(wiki_records or [])
    assets = []
    mapping_path = required_input(root, "data/sources/song-mapping.json")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    song_data = mapping.get("data") or {}
    songs, books = song_data.get("songs"), song_data.get("books")
    if not isinstance(songs, dict) or not isinstance(books, list):
        raise ValueError(f"Song mapping has no data.songs object and data.books list: {mapping_path.relative_to(root)}")
    original_dir = required_input(root, "assets/legacy/trans", directory=True)
    quantized_dir = required_input(root, "assets/legacy/tiny", directory=True)
    originals = legacy_pngs(original_dir)
    if {path.name for path in originals} != {path.name for path in legacy_pngs(quantized_dir)}:
        raise ValueError("Legacy trans/tiny filenames must be paired exactly")
    for path in originals:
        raw = path.read_bytes()
        with Image.open(path) as image:
            width, height = image.size
            format_name = image.format
        key = path.stem
        song, title_status = mapped_song(songs, key)
        titles, mapping_source = ([song["name"]] if song.get("name") else []), (mapping.get("source_url") if song else None)
        if not song and fold(key) in wiki_songs:
            (titles, mapping_source), title_status = wiki_songs[fold(key)], "mapped_wiki_file_key"
        book_index = song.get("book")
        book = books[book_index].get("name") if isinstance(book_index, int) and 0 <= book_index < len(books) else None
        is_cover = bool(COVER_KEY.search(key))
        asset = {
            "id": f"legacy:{key}", "source_id": "legacy",
            "title": song.get("name") or " / ".join(titles) or key, "internal_key": key, "artist": None,
            "composer": song.get("artist"), "collection": book,
            "song_titles": titles,
            "mapping_source": mapping_source,
            "kind": "song_art" if song or titles else "collection_cover" if is_cover else "illustration",
            "page_url": "https://github.com/mashirozx/deemo",
            "download_url": None, "path": path.relative_to(root).as_posix(),
            "width": width, "height": height, "format": format_name,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at": None, "game": "DEEMO", "title_status": title_status,
            "notes": "Inherited game texture; upstream made pure-white pixels transparent.",
        }
        quantized_path = quantized_dir / path.name
        quantized = quantized_path.read_bytes()
        with Image.open(quantized_path) as image:
            tiny_width, tiny_height = image.size
            tiny_format = image.format
        variant = {
            **asset, "id": f"legacy:tiny:{key}", "role": "palette_quantized",
            "path": quantized_path.relative_to(root).as_posix(),
            "url": quote(quantized_path.relative_to(root).as_posix(), safe="/"),
            "width": tiny_width, "height": tiny_height, "format": tiny_format,
            "bytes": len(quantized), "sha256": hashlib.sha256(quantized).hexdigest(),
            "notes": "Historical palette-quantized copy from the upstream tiny directory; retained as an alternative, not used for gallery display.",
        }
        asset["variants"] = [variant]
        assets.append(asset)
    return assets


def validate_asset(root: Path, asset: dict, verify: bool = False) -> None:
    for key in ("id", "source_id", "title", "kind", "page_url", "path", "bytes", "sha256"):
        if key not in asset:
            raise ValueError(f"Missing {key}: {asset.get('id')}")
    page_url = urlsplit(asset["page_url"])
    if page_url.scheme not in {"https", "http"} or not page_url.netloc:
        raise ValueError(f"Unsafe source URL: {asset['page_url']}")
    status = asset.get("upstream_status")
    if status is not None and status not in UPSTREAM_STATUSES:
        raise ValueError(f"Unknown upstream_status {status!r}: {asset['id']}")
    if (status == "superseded") != (asset.get("superseded_by") is not None):
        raise ValueError(f"superseded_by goes with upstream_status 'superseded', and only with it: {asset['id']}")
    path = safe_path(root, asset["path"])
    if path.stat().st_size != asset["bytes"]:
        raise ValueError(f"Byte count mismatch: {path}")
    if len(asset["sha256"]) != 64:
        raise ValueError(f"Invalid SHA-256: {path}")
    if verify:
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {path}")
        if str(asset.get("format", "")).upper() == "PDF":
            if not path.read_bytes().startswith(b"%PDF-"):
                raise ValueError(f"Invalid PDF header: {path}")
        else:
            with Image.open(path) as image:
                if list(image.size) != [asset.get("width"), asset.get("height")]:
                    raise ValueError(f"Dimension mismatch: {path}")
                if image.format != str(asset.get("format", "")).upper():
                    raise ValueError(f"Format mismatch: {path}")
                image.verify()
            with Image.open(path) as image:
                image.load()


def combine(root: Path, verify: bool = False) -> dict:
    sources = [{
        "id": "legacy", "name": "Original repository · game textures", "family": "legacy",
        "url": "https://github.com/mashirozx/deemo", "status": "inherited",
        "notes": "Original PNGs are in assets/legacy/trans/. Paired palette-quantized copies in assets/legacy/tiny/ are available as alternate downloads.",
    }]
    assets, failures, snapshots = [], [], {}
    for family in SOURCE_MANIFESTS:
        path = required_input(root, f"data/sources/{family}.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        snapshots[family] = manifest.get("fetched_at")
        sources.extend({**source, "family": family} for source in manifest["sources"])
        assets.extend({**asset, "family": family} for asset in manifest["assets"])
        failures.extend(manifest.get("failures", []))
    assets.extend({**asset, "family": "legacy"} for asset in legacy_assets(root, [asset for asset in assets if asset["family"] == "wikis"]))
    priority = {"artists": 0, "wikis": 1, "legacy": 2, "archives": 3}
    assets.sort(key=lambda asset: priority[asset["family"]])
    source_map = {source["id"]: source for source in sources}
    if len(source_map) != len(sources):
        raise ValueError("Duplicate source IDs across manifests")
    seen_ids, by_hash = set(), {}
    for asset in assets:
        validate_asset(root, asset, verify)
        for variant in asset.get("variants", []):
            validate_asset(root, variant, verify)
            variant["url"] = quote(variant["path"], safe="/")
        if asset["id"] in seen_ids:
            raise ValueError(f"Duplicate asset ID: {asset['id']}")
        seen_ids.add(asset["id"])
        if asset["source_id"] not in source_map:
            raise ValueError(f"Missing source: {asset['source_id']}")
        source = source_map[asset["source_id"]]
        provenance = {**asset, "source_name": source["name"]}
        if asset["sha256"] in by_hash:
            by_hash[asset["sha256"]]["provenance"].append(provenance)
        else:
            by_hash[asset["sha256"]] = {
                **asset, "source_name": source["name"], "provenance": [provenance],
            }
    for asset in assets:
        successor = asset.get("superseded_by")
        if successor is not None and (successor == asset["id"] or successor not in seen_ids):
            raise ValueError(f"superseded_by names no other asset: {asset['id']} -> {successor}")
    if verify:
        referenced = {Path(record["path"]).as_posix() for asset in assets for record in (asset, *asset.get("variants", []))}
        orphans = [name for name in tracked_files(root, ORPHAN_CHECK_DIRECTORIES) or [] if name not in referenced]
        if orphans:
            raise ValueError(f"{len(orphans)} tracked file(s) referenced by no manifest record or variant: {', '.join(orphans)}")
    entries = list(by_hash.values())
    for entry in entries:
        entry["gallery"] = bool(entry.get("width") and entry.get("height"))
        entry["url"] = quote(entry["path"], safe="/")
    counts = Counter(asset["family"] for asset in assets)
    return {
        "schema_version": 1, "owner": "FridrichMethod", "game": "DEEMO 1",
        "repository": "https://github.com/FridrichMethod/deemo",
        "snapshots": snapshots, "sources": sources, "assets": entries,
        "failures": failures,
        "summary": {
            "source_count": len(sources), "source_asset_records": len(assets),
            "unique_files": len(entries),
            "gallery_images": sum(entry["gallery"] for entry in entries),
            "exact_duplicate_records": len(assets) - len(entries),
            "legacy_quantized_files": sum(len(asset.get("variants", [])) for asset in assets if asset["family"] == "legacy"),
            "by_family": dict(counts), "failure_count": len(failures),
            "downloaded_bytes": sum(asset["bytes"] for asset in assets if asset["family"] != "legacy"),
        },
    }


def slide_records(asset: dict) -> list[dict]:
    """A catalog entry's provenance records, the caption's (the highest-priority family's) first; a bare record alone."""
    records = asset.get("provenance")
    return records if isinstance(records, list) and records else [asset]


def slide_notes(asset: dict) -> dict:
    """Liner-note fields for a slide, merged across provenance records: verbatim names, one per line, deduplicated.
    A collection name the title already spells out ("Sherwin collection", "Book of Alice — page 1") is left out.
    data-songs lists the mapped song titles, which the notes headline when the title is only a wiki file key."""
    found = {"songs": [], "composer": [], "artist": [], "collection": []}
    seen = {key: set() for key in found}
    title = fold(str(asset.get("title") or ""))

    def add(key: str, value: object) -> None:
        text = str(value or "").strip()
        folded = fold(text)
        if key == "collection" and folded and folded in title:
            return
        if text and folded not in seen[key]:
            seen[key].add(folded)
            found[key].append(text)

    for record in slide_records(asset):
        for value in record.get("song_titles") or []:
            add("songs", value)
        add("composer", record.get("composer"))
        add("artist", record.get("artist"))
        add("collection", record.get("collection"))
        for value in record.get("collections") or []:
            add("collection", value)
    return {f"data-{key}": "\n".join(values) for key, values in found.items() if values}


def art_layout(root: Path, asset: dict) -> dict:
    """Stage layout fields for a legacy game texture: a cut-out on a transparent canvas (upstream made the white
    transparent), often padded to 2048 x 1024 or 2048 x 2048 with the art off to one side, and sometimes cropped
    hard at a canvas edge. data-art-box is the box of the visibly opaque pixels (alpha above 24), as "x y w h"
    fractions of the canvas, so the stage can size and centre the art rather than the canvas. data-bleed names
    the edges the art is cut at (opaque along more than 2% of that edge), so the stage can soften the cut; a
    texture cut at every edge is a rectangular picture and keeps its edges. Other sources are framed pictures."""
    if asset.get("family") != "legacy":
        return {}
    with Image.open(safe_path(root, asset["path"])) as image:
        if "A" not in image.getbands():
            return {}
        opaque = image.getchannel("A").point([0] * 25 + [255] * 231)
    width, height = opaque.size
    box = opaque.getbbox()
    if not box:
        return {}
    fields = {}
    if box != (0, 0, width, height):
        left, top, right, bottom = box
        fractions = (left / width, top / height, (right - left) / width, (bottom - top) / height)
        fields["data-art-box"] = " ".join(f"{round(value, 4):g}" for value in fractions)
    bands = {"left": (0, 0, 2, height), "top": (0, 0, width, 2), "right": (width - 2, 0, width, height), "bottom": (0, height - 2, width, height)}
    cut = []
    for edge, band in bands.items():
        strip = opaque.crop(band)
        if strip.histogram()[255] > .02 * strip.width * strip.height:
            cut.append(edge)
    if cut and len(cut) < 4:
        fields["data-bleed"] = " ".join(cut)
    return fields


def slide_kind(asset: dict) -> str:
    """The kind the slide's eyebrow names. An unmapped legacy texture is "illustration" in the catalog only as the
    archive's "Illustration / unmapped" catch-all, and most of them are song textures, so its slide says "unmapped",
    a kind with no label: the eyebrow then names no kind rather than call it an illustration."""
    if asset.get("family") == "legacy" and asset.get("title_status") == "internal_key" and asset["kind"] == "illustration":
        return "unmapped"
    return asset["kind"]


def slide_provenance(asset: dict) -> dict:
    """data-provenance: the provenance class of the record the slide's caption credits (the entry's first record, from
    the highest-priority family), which the slideshow names next to the kind. A wiki upload, whose manifest states its
    lineage caveat as prose, is "wiki"; an archives record carries a class token (community_repost, community_scan,
    official_website, ...). Artist uploads and legacy textures carry none."""
    record = slide_records(asset)[0]
    if record.get("family") == "wikis":
        return {"data-provenance": "wiki"}
    token = record.get("provenance")
    return {"data-provenance": token} if isinstance(token, str) and re.fullmatch(r"[a-z][a-z_]*", token) else {}


def slide_composer_source(asset: dict, names: dict) -> dict:
    """data-composer-sources: who gives each composer credit on the slide. Records spell a credit differently
    ("Narsil (from Ring) feat. ..." on BWIKI, "... Feat. ..." on Fandom), so data-composer lists one line per spelling
    (slide_notes()), and the notes would otherwise read every line as the caption's source's. For each line, in order,
    this is a JSON list of the [source id, name] pairs whose pages give it, or [] when the source the caption credits
    (the entry's first record) is one of them. A wiki record whose own song page names no composer gives the other
    wiki's credit (composer_source). The slideshow localizes each name by its id, as it does the caption's source;
    with no line to attribute there is no attribute."""
    records = slide_records(asset)
    lines: dict[str, list[str]] = {}
    for record in records:
        text = str(record.get("composer") or "").strip()
        if not text:
            continue
        suppliers = lines.setdefault(fold(text), [])
        for source in str(record.get("composer_source") or record.get("source_id") or "").split(" / "):
            if source and source not in suppliers:
                suppliers.append(source)
    caption = records[0].get("source_id")
    credits = [[] if caption in suppliers else [[source, names.get(source, source)] for source in suppliers]
               for suppliers in lines.values()]
    if not any(credits):
        return {}
    return {"data-composer-sources": json.dumps(credits, ensure_ascii=False, separators=(",", ":"))}


def render_slideshow(root: Path, catalog: dict) -> str:
    template = (root / "templates/slideshow.html").read_text(encoding="utf-8-sig")
    assets = [asset for asset in catalog["assets"] if asset["gallery"] and asset["kind"] != "reference"]
    names = {source["id"]: source["name"] for source in catalog.get("sources", [])}
    # Decoding the textures dominates the build; Pillow decodes outside the GIL, so threads share the work.
    with ThreadPoolExecutor() as pool:
        layouts = list(pool.map(lambda asset: art_layout(root, asset), assets))
    slides = []
    for asset, layout in zip(assets, layouts):
        fields = {
            "class": "deemo-draw", "data-src": asset["url"],
            "data-id": asset["id"], "data-title": asset["title"],
            "data-source": asset["source_name"], "data-source-id": asset["source_id"],
            "data-page": asset["page_url"],
            "data-size": f"{asset['width']} × {asset['height']}", "data-kind": slide_kind(asset),
            **slide_provenance(asset), **layout, **slide_notes(asset), **slide_composer_source(asset, names),
            "alt": asset["title"],
        }
        attributes = " ".join(f'{key}="{attr(value)}"' for key, value in fields.items())
        slides.append(f'        <div class="mySlides fade"><img {attributes}></div>')
    return template.replace("@python-work-area", "\n".join(slides))


def attach_thumbnails(root: Path, catalog: dict, verify: bool = False, required: bool = True) -> None:
    """Add `thumb`, the derived grid preview listed in data/thumbs.json (scripts/build_thumbnails.py), to gallery
    entries. Previews are not archive files: `url` stays the original, and entries without a preview get no field.
    The repository's build requires the manifest, so a missing one fails rather than quietly dropping every preview;
    only a caller that builds without previews (a test fixture) passes required=False."""
    if not required and not (root / "data" / "thumbs.json").exists():
        return
    manifest = required_input(root, "data/thumbs.json")
    thumbnails = json.loads(manifest.read_text(encoding="utf-8"))["thumbnails"]
    for entry in catalog["assets"]:
        thumb = thumbnails.get(entry["sha256"]) if entry["gallery"] else None
        if not thumb:
            continue
        if not thumb["path"].startswith("assets/thumbs/"):
            raise ValueError(f"Thumbnail outside assets/thumbs/: {thumb['path']}")
        path = safe_path(root, thumb["path"])
        if not path.is_file() or path.stat().st_size != thumb["bytes"]:
            raise ValueError(f"Missing or stale thumbnail {thumb['path']}: run scripts/build_thumbnails.py")
        if verify and hashlib.sha256(path.read_bytes()).hexdigest() != thumb["sha256"]:
            raise ValueError(f"Thumbnail SHA-256 mismatch: {thumb['path']}")
        entry["thumb"] = quote(thumb["path"], safe="/")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify every hash, image header and format, and that every tracked file under assets/public and assets/legacy is referenced")
    parser.add_argument("--check", action="store_true", help="Check generated files without writing")
    args = parser.parse_args(argv)
    catalog = combine(ROOT, args.verify)
    attach_thumbnails(ROOT, catalog, args.verify)
    encoded = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    script = "window.DEEMO_CATALOG = " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"
    outputs = {
        ROOT / "data" / "catalog.json": encoded,
        ROOT / "data" / "catalog.js": script,
        ROOT / "index.html": render_slideshow(ROOT, catalog),
    }
    for path, content in outputs.items():
        if args.check:
            # Decode the raw bytes: no newline translation (read_text only takes newline= from Python 3.13), so a
            # stray CR is never read back as LF. The outputs themselves never contain a CR (attr() and JSON escape
            # it), so folding CRLF only accepts a Windows core.autocrlf checkout.
            if not path.is_file() or path.read_bytes().decode("utf-8").replace("\r\n", "\n") != content:
                raise SystemExit(f"Out-of-date generated file: {path.relative_to(ROOT).as_posix()}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
