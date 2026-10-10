"""Build the archive grid's derived WebP previews without modifying any original file.

Every gallery image whose long edge exceeds LONG_EDGE gets one preview in assets/thumbs/, named by the first
16 hex digits of the original's SHA-256 and recorded in data/thumbs.json. Previews are display copies for the
grid only, not archive files: the catalog keeps each original's path, bytes and hash, and the viewer, download
and original links still serve the original. Smaller images, PDFs and other non-image files get no preview.
With the Pillow pinned in requirements.txt a rebuild writes byte-identical files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import PIL
from PIL import Image, ImageCms, ImageOps, features

ROOT = Path(__file__).resolve().parents[1]
THUMB_DIR = "assets/thumbs"
MANIFEST = "data/thumbs.json"
# Grid tiles are about 165-358 CSS px wide (src/archive.css: minmax(230px, 1fr) columns, two of them at 760 px and
# narrower): about 235-290 px in desktop windows, 165-195 px on phones and up to 358 px on tablets and narrow windows.
# A 480 px long edge is close to 1:1 for typical desktop tiles at 2x; the widest tiles at 2x and large phones at 3x
# are upscaled by up to about 1.5x and 1.2x. That is a deliberate size trade-off: larger previews would take a bigger
# share of the 950 MB Pages budget.
LONG_EDGE = 480
QUALITY = 75
METHOD = 6  # libwebp's slowest, smallest setting; deterministic like the others
NAME_DIGITS = 16
VERSION_KEYS = ("pillow", "libwebp")
# Modes whose plain conversion to 8-bit RGB(A) keeps their meaning. 16-bit greyscale is scaled down first; anything else
# (CMYK, 32-bit integer or float, YCbCr, ...) is refused rather than previewed with wrong colours.
EIGHT_BIT_MODES = {"1", "L", "LA", "P", "PA", "RGB", "RGBA"}
GREY_MODES = {"1", "L", "LA"}
SIXTEEN_BIT_GREY = {"I;16", "I;16L", "I;16B", "I;16N"}
DESCRIPTION = ("Derived WebP previews for the archive grid only; they are not archive files. "
               "Originals are unchanged and listed with their hashes in data/catalog.json.")


def _load_catalog_builder():
    spec = importlib.util.spec_from_file_location("build_catalog", Path(__file__).resolve().with_name("build_catalog.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_catalog = _load_catalog_builder()


def settings() -> dict:
    return {"format": "WEBP", "long_edge": LONG_EDGE, "quality": QUALITY, "method": METHOD,
            "pillow": PIL.__version__, "libwebp": features.version("webp")}


def stable(encoder: dict) -> dict:
    """Encoder settings without library versions."""
    return {key: value for key, value in encoder.items() if key not in VERSION_KEYS}


def thumb_size(width: int, height: int) -> tuple[int, int] | None:
    """Preview size with the long edge scaled to LONG_EDGE, or None when the original is small enough already."""
    long_edge = max(width, height)
    if long_edge <= LONG_EDGE:
        return None
    return max(1, round(width * LONG_EDGE / long_edge)), max(1, round(height * LONG_EDGE / long_edge))


def thumb_path(sha256: str) -> str:
    return f"{THUMB_DIR}/{sha256[:NAME_DIGITS]}.webp"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def originals(root: Path) -> dict[str, dict]:
    """Gallery images that need a preview, keyed by SHA-256 (the catalog already merges byte-identical files)."""
    wanted = {}
    for entry in build_catalog.combine(root)["assets"]:
        if not entry["gallery"] or str(entry.get("format", "")).upper() == "PDF":
            continue
        if thumb_size(entry["width"], entry["height"]):
            wanted[entry["sha256"]] = {key: entry[key] for key in ("path", "width", "height")}
    names = {}
    for sha256, original in wanted.items():
        other = names.setdefault(thumb_path(sha256), original["path"])
        if other != original["path"]:
            raise ValueError(f"Thumbnail name collision: {original['path']} and {other}")
    return dict(sorted(wanted.items()))


def eight_bit(image: Image.Image, path: str) -> Image.Image:
    """The image in a mode that converts to RGB(A) exactly. 16-bit greyscale is scaled to 8 bits as browsers show it;
    a plain conversion would clip it to near white."""
    if image.mode in SIXTEEN_BIT_GREY and "transparency" not in image.info:
        return image.convert("I").point(lambda value: value / 257 + 0.5).convert("L")
    if image.mode not in EIGHT_BIT_MODES:
        raise ValueError(f"Cannot preview {path}: unsupported image mode {image.mode}")
    return image


def colour_managed(image: Image.Image, path: str) -> tuple[Image.Image, bytes | None]:
    """The image and the ICC profile to embed in its preview. An RGB profile on a colour image is kept, so the preview's
    colours match the original's. A greyscale profile on a greyscale image is applied instead, giving untagged sRGB
    pixels, which browsers show as sRGB. Any other profile is refused: an RGB preview cannot carry it."""
    icc = image.info.get("icc_profile")
    if not icc:
        return image, None
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except (OSError, ImageCms.PyCMSError) as error:
        raise ValueError(f"Cannot preview {path}: unreadable ICC profile ({error})") from error
    space = profile.profile.xcolor_space.strip()
    if space == "RGB" and image.mode not in GREY_MODES:
        return image, icc
    if space != "GRAY" or image.mode not in GREY_MODES:
        raise ValueError(f"Cannot preview {path}: {space} ICC profile on a {image.mode} image")
    alpha = image.mode == "LA" or "transparency" in image.info
    grey = image.convert("LA" if alpha else "L")
    try:
        # A fresh sRGB profile on every call, so worker threads never share a LittleCMS profile handle.
        rgb = ImageCms.profileToProfile(grey.convert("L"), profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except ImageCms.PyCMSError as error:
        raise ValueError(f"Cannot preview {path}: its greyscale ICC profile cannot be applied ({error})") from error
    if alpha:
        rgb.putalpha(grey.getchannel("A"))
    return rgb, None


def render(root: Path, original: dict) -> tuple[bytes, tuple[int, int]]:
    """Downscale one original to a WebP preview. No EXIF or XMP is written; colour_managed() decides the ICC profile."""
    with Image.open(build_catalog.safe_path(root, original["path"])) as image:
        image.load()
        # Browsers show originals upright, so the preview applies the EXIF orientation too.
        ImageOps.exif_transpose(image, in_place=True)
        image, icc_profile = colour_managed(eight_bit(image, original["path"]), original["path"])
        size = thumb_size(*image.size)
        if size is None:
            raise ValueError(f"Original is not larger than {LONG_EDGE} px: {original['path']}")
        alpha = image.mode in {"RGBA", "LA", "PA", "RGBa", "La"} or "transparency" in image.info
        target = "RGBA" if alpha else "RGB"
        if image.mode != target:
            image = image.convert(target)
        # Pillow resamples RGBA premultiplied, so fully transparent pixels do not darken the cut-out edges.
        preview = image.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
    buffer = io.BytesIO()
    preview.save(buffer, "WEBP", quality=QUALITY, method=METHOD, icc_profile=icc_profile)
    return buffer.getvalue(), preview.size


def read_manifest(root: Path) -> dict | None:
    path = root / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def stored_files(root: Path) -> list[str]:
    directory = root / THUMB_DIR
    if not directory.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in directory.rglob("*") if path.is_file())


def workers_for(workers: int | None) -> int:
    # Each worker holds a decoded original (up to about 50 MB), so the pool stays small.
    return workers or min(8, os.cpu_count() or 1)


def build(root: Path, prune: bool = False, rebuild: bool = False, workers: int | None = None) -> dict:
    """Write missing or stale previews and data/thumbs.json; reuse previews whose bytes match the manifest."""
    current = settings()
    manifest = read_manifest(root)
    if manifest and manifest.get("encoder") != current and not rebuild:
        raise ValueError(f"{MANIFEST} was encoded with {manifest.get('encoder')}, but this run uses {current}. "
                         "Install requirements.txt, or pass --rebuild to re-encode every thumbnail.")
    previous = manifest["thumbnails"] if manifest and not rebuild else {}
    entries, pending = {}, []
    for sha256, original in originals(root).items():
        old, path = previous.get(sha256), root / thumb_path(sha256)
        if old and old["path"] == thumb_path(sha256) and path.is_file() and digest(path.read_bytes()) == old["sha256"]:
            entries[sha256] = old
        else:
            pending.append((sha256, original))
    with ThreadPoolExecutor(workers_for(workers)) as pool:
        for (sha256, _), (data, (width, height)) in zip(pending, pool.map(lambda item: render(root, item[1]), pending)):
            write_atomic(root / thumb_path(sha256), data)
            entries[sha256] = {"path": thumb_path(sha256), "width": width, "height": height,
                               "bytes": len(data), "sha256": digest(data)}
    content = {"schema_version": 1, "description": DESCRIPTION, "encoder": current,
               "thumbnails": dict(sorted(entries.items()))}
    encoded = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not (root / MANIFEST).exists() or (root / MANIFEST).read_bytes() != encoded:
        write_atomic(root / MANIFEST, encoded)
    referenced = {entry["path"] for entry in entries.values()}
    orphans = [path for path in stored_files(root) if path not in referenced]
    if prune:
        for path in orphans:
            (root / path).unlink()
    return {"thumbnails": len(entries), "rendered": len(pending), "reused": len(entries) - len(pending),
            "bytes": sum(entry["bytes"] for entry in entries.values()),
            "orphans": [] if prune else orphans, "pruned": orphans if prune else []}


def check_entry(root: Path, sha256: str, entry: dict, original: dict | None) -> tuple[list[str], bytes | None]:
    """Problems with one recorded preview, and its bytes when they are intact."""
    path = entry.get("path")
    if path != thumb_path(sha256):
        return [f"Thumbnail for {sha256} should be {thumb_path(sha256)}, not {path}"], None
    file = root / path
    if not file.is_file():
        return [f"Missing thumbnail file: {path}: run scripts/build_thumbnails.py"], None
    data = file.read_bytes()
    if len(data) != entry.get("bytes") or digest(data) != entry.get("sha256"):
        return [f"Thumbnail size or SHA-256 differs from {MANIFEST}: {path}"], None
    try:
        with Image.open(io.BytesIO(data)) as image:
            format_name, size, info = image.format, image.size, dict(image.info)
            image.load()
    except Exception as error:  # noqa: BLE001 - any decoder failure is a broken preview
        return [f"Thumbnail does not decode: {path}: {error}"], None
    found = []
    if format_name != "WEBP":
        found.append(f"Thumbnail is {format_name}, not WEBP: {path}")
    if [*size] != [entry.get("width"), entry.get("height")]:
        found.append(f"Dimension mismatch: {path} is {size[0]}x{size[1]}, {MANIFEST} says {entry.get('width')}x{entry.get('height')}")
    if original:
        # Either orientation: an EXIF-rotated original is previewed upright.
        expected = thumb_size(original["width"], original["height"])
        if sorted(size) != sorted(expected):
            found.append(f"Dimension mismatch: {path} is {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]} for {original['path']}")
    if "exif" in info or "xmp" in info:
        found.append(f"Thumbnail carries EXIF or XMP metadata: {path}")
    return found, data


def problems(root: Path, reencode: bool = False, workers: int | None = None) -> list[str]:
    """Everything that differs from what a build would produce; empty when the previews are current.
    With reencode, every preview is also rendered again from its original and must match byte for byte."""
    manifest = read_manifest(root)
    if manifest is None:
        return [f"{MANIFEST} is missing: run scripts/build_thumbnails.py"]
    current, recorded = settings(), manifest.get("encoder", {})
    found = []
    if stable(recorded) != stable(current):
        found.append(f"Encoder settings changed: {MANIFEST} has {stable(recorded)}, the script uses {stable(current)}: "
                     "run scripts/build_thumbnails.py --rebuild")
    if reencode and recorded != current:
        found.append(f"Byte-identical re-encoding needs Pillow {recorded.get('pillow')} with libwebp {recorded.get('libwebp')} "
                     f"(running Pillow {current['pillow']} with libwebp {current['libwebp']}); install requirements.txt")
        reencode = False
    wanted, thumbnails = originals(root), manifest.get("thumbnails", {})
    for sha256 in sorted(wanted.keys() - thumbnails.keys()):
        found.append(f"No thumbnail for {wanted[sha256]['path']}: run scripts/build_thumbnails.py")
    for sha256 in sorted(thumbnails.keys() - wanted.keys()):
        found.append(f"Thumbnail {thumbnails[sha256].get('path')} belongs to no current gallery original: "
                     "run scripts/build_thumbnails.py --prune")
    intact = []
    for sha256, entry in sorted(thumbnails.items()):
        entry_problems, data = check_entry(root, sha256, entry, wanted.get(sha256))
        found.extend(entry_problems)
        if data is not None and sha256 in wanted:
            intact.append((wanted[sha256], entry["path"], data))
    referenced = {entry.get("path") for entry in thumbnails.values()}
    found.extend(f"Unreferenced file in {THUMB_DIR}: {path}: run scripts/build_thumbnails.py --prune"
                 for path in stored_files(root) if path not in referenced)
    if reencode:
        with ThreadPoolExecutor(workers_for(workers)) as pool:
            rendered = pool.map(lambda item: render(root, item[0])[0], intact)
            found.extend(f"Re-encoding {original['path']} does not reproduce {path}"
                         for (original, path, data), fresh in zip(intact, rendered) if fresh != data)
    return found


def main(argv: list[str] | None = None, root: Path = ROOT) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Write nothing; fail unless every gallery original has its preview and each preview exists, "
                           "decodes, has the expected size and hash, and nothing in assets/thumbs/ is orphaned")
    mode.add_argument("--verify", action="store_true",
                      help="Like --check, and also re-encode every preview from its original and require identical bytes")
    parser.add_argument("--prune", action="store_true", help="Delete previews whose original is no longer in the gallery")
    parser.add_argument("--rebuild", action="store_true",
                        help="Re-encode every preview (after changing the encoder settings or the pinned Pillow)")
    parser.add_argument("--workers", type=int, help="Images decoded in parallel (default: up to 8)")
    args = parser.parse_args(argv)
    if (args.check or args.verify) and (args.prune or args.rebuild):
        parser.error("--prune and --rebuild write files; they cannot be combined with --check or --verify")
    if args.check or args.verify:
        found = problems(root, reencode=args.verify, workers=args.workers)
        if found:
            raise SystemExit("\n".join(found))
        thumbnails = read_manifest(root)["thumbnails"]
        print(json.dumps({"thumbnails": len(thumbnails), "bytes": sum(entry["bytes"] for entry in thumbnails.values()),
                          "reencoded": args.verify}, indent=2))
        return
    print(json.dumps(build(root, prune=args.prune, rebuild=args.rebuild, workers=args.workers), indent=2))


if __name__ == "__main__":
    main()
