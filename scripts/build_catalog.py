"""Build the catalog and slideshow without modifying any image bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFESTS = ("artists", "wikis", "archives")


def attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&#39;", "\n": "&#10;"})


def safe_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Unsafe asset path: {value}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Asset outside repository: {value}")
    if relative.parts[0] != "assets":
        raise ValueError(f"Unexpected asset directory: {value}")
    return path


def required_input(root: Path, relative: str, directory: bool = False) -> Path:
    """An input every build needs: a missing one fails loudly rather than quietly building a smaller site."""
    path = root / relative
    if not (path.is_dir() if directory else path.is_file()):
        raise FileNotFoundError(f"Missing required input: {relative}")
    return path


def legacy_pngs(directory: Path) -> list[Path]:
    """A legacy directory's PNGs in file-name order. The explicit key and the exact suffix test give the same list
    on every platform (WindowsPath sorts case-folded, and glob matches case-insensitively on Windows)."""
    return sorted((path for path in directory.iterdir() if path.suffix == ".png"), key=lambda path: path.name)


def legacy_assets(root: Path) -> list[dict]:
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
        song = songs.get(key, {})
        book_index = song.get("book")
        book = books[book_index].get("name") if isinstance(book_index, int) and 0 <= book_index < len(books) else None
        is_cover = any(word in key.lower() for word in ("booksprite", "bookcover"))
        asset = {
            "id": f"legacy:{key}", "source_id": "legacy",
            "title": song.get("name", key), "internal_key": key, "artist": None,
            "composer": song.get("artist"), "collection": book,
            "song_titles": [song["name"]] if song.get("name") else [],
            "mapping_source": mapping.get("source_url") if song else None,
            "kind": "song_art" if song else "collection_cover" if is_cover else "illustration",
            "page_url": "https://github.com/mashirozx/deemo",
            "download_url": None, "path": path.relative_to(root).as_posix(),
            "width": width, "height": height, "format": format_name,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at": None, "game": "DEEMO", "title_status": "mapped_exact_internal_key" if song else "internal_key",
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
    assets.extend({**asset, "family": "legacy"} for asset in legacy_assets(root))
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


def slide_notes(asset: dict) -> dict:
    """Liner-note fields for a slide, merged across provenance records: verbatim names, one per line, deduplicated.
    A collection name the title already spells out ("Sherwin collection", "Book of Alice — page 1") is left out."""
    found = {"composer": [], "artist": [], "collection": []}
    seen = {key: set() for key in found}

    def fold(text: str) -> str:
        return "".join(char for char in text.casefold() if char.isalnum())

    title = fold(str(asset.get("title") or ""))

    def add(key: str, value: object) -> None:
        text = str(value or "").strip()
        folded = fold(text)
        if key == "collection" and folded and folded in title:
            return
        if text and folded not in seen[key]:
            seen[key].add(folded)
            found[key].append(text)

    for record in asset.get("provenance") or [asset]:
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


def render_slideshow(root: Path, catalog: dict) -> str:
    template = (root / "templates/slideshow.html").read_text(encoding="utf-8-sig")
    assets = [asset for asset in catalog["assets"] if asset["gallery"] and asset["kind"] != "reference"]
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
            "data-size": f"{asset['width']} × {asset['height']}", "data-kind": asset["kind"],
            **layout, **slide_notes(asset), "alt": asset["title"],
        }
        attributes = " ".join(f'{key}="{attr(value)}"' for key, value in fields.items())
        slides.append(f'        <div class="mySlides fade"><img {attributes}></div>')
    return template.replace("@python-work-area", "\n".join(slides))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify every hash, image header and format")
    parser.add_argument("--check", action="store_true", help="Check generated files without writing")
    args = parser.parse_args()
    catalog = combine(ROOT, args.verify)
    encoded = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    script = "window.DEEMO_CATALOG = " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"
    outputs = {
        ROOT / "data" / "catalog.json": encoded,
        ROOT / "data" / "catalog.js": script,
        ROOT / "index.html": render_slideshow(ROOT, catalog),
    }
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Out-of-date generated file: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
