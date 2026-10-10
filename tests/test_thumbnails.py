"""Derived grid thumbnails: deterministic output, drift detection, pruning and the catalog hook."""

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageCms, ImageStat

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


thumbs = load("build_thumbnails")
catalog_builder = load("build_catalog")


def pattern(size, mode):
    """A deterministic image with detail in every channel, so the encoder has real work to do."""
    width, height = size
    channels = [
        Image.linear_gradient("L").resize(size),
        Image.radial_gradient("L").resize(size),
        Image.linear_gradient("L").rotate(90).resize(size),
    ]
    image = Image.merge("RGB", channels)
    if mode == "RGBA":
        alpha = Image.new("L", size, 255)
        alpha.paste(0, (0, 0, width // 3, height))
        image.putalpha(alpha)
    return image


SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def grey_profile(gamma):
    """A minimal ICC v2 greyscale display profile (a gamma curve and a D50 white point); Pillow can only create RGB,
    Lab and XYZ profiles."""
    def xyz(x, y, z):
        return b"XYZ " + bytes(4) + struct.pack(">3i", *(round(value * 65536) for value in (x, y, z)))
    tags = [(b"kTRC", b"curv" + bytes(4) + struct.pack(">IH", 1, round(gamma * 256))), (b"wtpt", xyz(0.9642, 1.0, 0.8249))]
    offset, table, body = 128 + 4 + 12 * len(tags), b"", b""
    for signature, data in tags:
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    header = (struct.pack(">I", offset + len(body)) + bytes(4) + bytes([2, 0x10, 0, 0]) + b"mntrGRAYXYZ " + bytes(12)
              + b"acsp" + bytes(28) + xyz(0.9642, 1.0, 0.8249)[8:] + bytes(48))
    return header + struct.pack(">I", len(tags)) + table + body


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture(unittest.TestCase):
    """A miniature checkout: one artist manifest with large, small and non-image files."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "checkout"
        (self.root / "data/sources").mkdir(parents=True)
        # Every input scripts/build_catalog.py requires: the other source manifests, the song mapping and both
        # legacy directories, all empty, so combine() sees only the artist records below.
        for family in ("wikis", "archives"):
            (self.root / f"data/sources/{family}.json").write_text(json.dumps({"sources": [], "assets": []}), encoding="utf-8")
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps({"data": {"songs": {}, "books": []}}), encoding="utf-8")
        for directory in ("assets/legacy/trans", "assets/legacy/tiny"):
            (self.root / directory).mkdir(parents=True)
        self.records = []
        self.add_image("wide.png", (1000, 600), "RGB", "PNG")
        self.add_image("cutout.png", (700, 900), "RGBA", "PNG")
        self.add_image("photo.jpg", (800, 800), "RGB", "JPEG")
        self.add_image("small.png", (300, 200), "RGB", "PNG")
        self.add_file("booklet.pdf", b"%PDF-1.4\n% fixture\n", {"format": "PDF"})
        self.write_manifest()

    def add_file(self, name, raw, extra):
        path = self.root / "assets/public/artists" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.records.append({
            "id": f"artist:{name}", "source_id": "artist", "title": name, "kind": "song_art",
            "page_url": "https://example.com/art", "path": path.relative_to(self.root).as_posix(),
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "game": "DEEMO", **extra,
        })

    def add_image(self, name, size, mode, format_name):
        image = pattern(size, mode)
        if format_name == "JPEG":
            # EXIF must not reach the preview; the colour profile must, so colours match the original.
            exif = Image.Exif()
            exif[0x010E] = "fixture description"
            self.add_encoded(name, image, format_name, quality=90, exif=exif, icc_profile=SRGB)
        else:
            self.add_encoded(name, image, format_name)

    def add_encoded(self, name, image, format_name, **options):
        """Add any image as an original; like the fetchers, the record holds the stored (unrotated) size."""
        buffer = io.BytesIO()
        image.save(buffer, format_name, **options)
        self.add_file(name, buffer.getvalue(), {"width": image.width, "height": image.height, "format": format_name})
        return self.records[-1]["path"]

    def write_manifest(self, records=None):
        content = {"sources": [{"id": "artist", "name": "Artist", "url": "https://example.com"}], "assets": records if records is not None else self.records}
        (self.root / "data/sources/artists.json").write_text(json.dumps(content), encoding="utf-8")

    def sha(self, name):
        return next(record["sha256"] for record in self.records if record["path"].endswith("/" + name))

    def manifest(self, root=None):
        return json.loads(((root or self.root) / thumbs.MANIFEST).read_text(encoding="utf-8"))

    def files(self, root=None):
        directory = (root or self.root) / thumbs.THUMB_DIR
        return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}

    def copy(self, name):
        target = self.base / name
        shutil.copytree(self.root, target)
        return target


class BuildTests(Fixture):
    def test_builds_are_byte_identical_across_checkouts_and_reruns(self):
        other = self.copy("other")
        thumbs.build(self.root)
        thumbs.build(other)
        first = self.files()
        self.assertEqual(first, self.files(other))
        self.assertEqual((self.root / thumbs.MANIFEST).read_bytes(), (other / thumbs.MANIFEST).read_bytes())
        # A rerun reuses every preview and rewrites nothing.
        before = {path: path.stat().st_mtime_ns for path in (self.root / thumbs.THUMB_DIR).iterdir()}
        summary = thumbs.build(self.root)
        self.assertEqual(summary["rendered"], 0)
        self.assertEqual({path: path.stat().st_mtime_ns for path in before}, before)
        # Deleting the previews and building again reproduces the same bytes.
        shutil.rmtree(self.root / thumbs.THUMB_DIR)
        self.assertEqual(thumbs.build(self.root)["rendered"], 3)
        self.assertEqual(self.files(), first)
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])

    def test_a_preview_that_no_longer_matches_the_manifest_is_rendered_again(self):
        thumbs.build(self.root)
        first, manifest = self.files(), (self.root / thumbs.MANIFEST).read_bytes()
        path = self.root / self.manifest()["thumbnails"][self.sha("wide.png")]["path"]
        # Same size, one byte flipped: only the recorded SHA-256 tells the damaged preview apart.
        damaged = bytearray(path.read_bytes())
        damaged[len(damaged) // 2] ^= 0xFF
        path.write_bytes(bytes(damaged))
        summary = thumbs.build(self.root)
        self.assertEqual((summary["rendered"], summary["reused"]), (1, 2))
        self.assertEqual(self.files(), first)
        self.assertEqual((self.root / thumbs.MANIFEST).read_bytes(), manifest)
        self.assertEqual(thumbs.problems(self.root), [])

    def test_previews_are_small_metadata_free_webp_files(self):
        thumbs.build(self.root)
        entries = self.manifest()["thumbnails"]
        # The small PNG keeps using its original, and the PDF is not an image.
        self.assertEqual(set(entries), {self.sha("wide.png"), self.sha("cutout.png"), self.sha("photo.jpg")})
        expected = {"wide.png": (480, 288), "cutout.png": (373, 480), "photo.jpg": (480, 480)}
        for name, size in expected.items():
            with self.subTest(name=name):
                entry = entries[self.sha(name)]
                self.assertEqual(entry["path"], f"assets/thumbs/{self.sha(name)[:16]}.webp")
                path = self.root / entry["path"]
                self.assertEqual(entry["sha256"], digest(path))
                self.assertEqual(entry["bytes"], path.stat().st_size)
                with Image.open(path) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.size, size)
                    self.assertEqual([entry["width"], entry["height"]], list(size))
                    self.assertNotIn("exif", image.info)
                    self.assertNotIn("xmp", image.info)
                    self.assertEqual(image.info.get("icc_profile"), SRGB if name == "photo.jpg" else None)
                    if name == "cutout.png":
                        self.assertEqual(image.mode, "RGBA")
                        self.assertEqual(image.getpixel((5, 240))[3], 0)
                        self.assertEqual(image.getpixel((370, 240))[3], 255)
        encoder = self.manifest()["encoder"]
        self.assertEqual((encoder["format"], encoder["long_edge"]), ("WEBP", 480))
        self.assertIn("pillow", encoder)
        self.assertIn("libwebp", encoder)

    def test_originals_are_never_modified(self):
        before = {record["path"]: digest(self.root / record["path"]) for record in self.records}
        thumbs.build(self.root, prune=True)
        self.assertEqual({path: digest(self.root / path) for path in before}, before)

    def test_exif_orientation_gives_an_upright_preview(self):
        # Upright, the photo is a 600x1000 portrait with a red top band. It is stored rotated a quarter turn
        # counter-clockwise, 1000x600, with EXIF Orientation 6 telling viewers to turn it clockwise.
        upright = Image.new("RGB", (600, 1000), (0, 0, 255))
        upright.paste((255, 0, 0), (0, 0, 600, 250))
        exif = Image.Exif()
        exif[0x0112] = 6
        self.add_encoded("rotated.jpg", upright.transpose(Image.Transpose.ROTATE_90), "JPEG", quality=95, exif=exif)
        self.write_manifest()
        thumbs.build(self.root)
        entry = self.manifest()["thumbnails"][self.sha("rotated.jpg")]
        self.assertEqual((entry["width"], entry["height"]), (288, 480))
        with Image.open(self.root / entry["path"]) as image:
            self.assertEqual(image.size, (288, 480))
            self.assertNotIn("exif", image.info)  # a kept Orientation tag would turn the upright preview again
            top, bottom = image.convert("RGB").getpixel((144, 20)), image.convert("RGB").getpixel((144, 460))
        self.assertTrue(top[0] > 200 and top[2] < 60, top)
        self.assertTrue(bottom[2] > 200 and bottom[0] < 60, bottom)
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])

    def test_changed_encoder_settings_need_an_explicit_rebuild(self):
        thumbs.build(self.root)
        manifest = self.manifest()
        manifest["encoder"]["quality"] = 1
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--rebuild"):
            thumbs.build(self.root)
        self.assertEqual(thumbs.build(self.root, rebuild=True)["rendered"], 3)
        self.assertEqual(thumbs.problems(self.root), [])


class ColourModeTests(Fixture):
    """Originals in uncommon modes or with non-RGB colour profiles are previewed faithfully, or refused."""

    def render(self, name, image, format_name="PNG", **options):
        data, _ = thumbs.render(self.root, {"path": self.add_encoded(name, image, format_name, **options)})
        preview = Image.open(io.BytesIO(data))
        preview.load()
        return preview

    def test_sixteen_bit_greyscale_keeps_its_tones(self):
        tones = Image.linear_gradient("L").resize((800, 600))
        # 16-bit PNGs open in mode I;16; a plain conversion to RGB would clip them to near white.
        preview = self.render("deep.png", tones.convert("I").point(lambda value: value * 257).convert("I;16"))
        self.assertEqual((preview.mode, preview.size), ("RGB", (480, 360)))
        self.assertAlmostEqual(ImageStat.Stat(preview).mean[0], ImageStat.Stat(tones).mean[0], delta=2)
        low, high = preview.getextrema()[0]
        self.assertLess(low, 10)
        self.assertGreater(high, 245)

    def test_greyscale_profile_is_applied_not_embedded(self):
        # A linear (gamma 1.0) greyscale profile: mid-grey 128 is much lighter in sRGB, so applying it is visible.
        flat = Image.new("L", (800, 600), 128)
        expected = ImageCms.profileToProfile(flat, ImageCms.ImageCmsProfile(io.BytesIO(grey_profile(1.0))),
                                             ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((0, 0))
        self.assertGreater(expected[0], 170)
        for name, mode, format_name in (("grey.jpg", "L", "JPEG"), ("grey-cutout.png", "LA", "PNG")):
            with self.subTest(mode=mode):
                image = flat.convert(mode)
                if mode == "LA":
                    alpha = Image.new("L", flat.size, 255)
                    alpha.paste(0, (0, 0, 200, 600))
                    image.putalpha(alpha)
                preview = self.render(name, image, format_name, icc_profile=grey_profile(1.0))
                self.assertNotIn("icc_profile", preview.info)
                self.assertEqual(preview.mode, "RGBA" if mode == "LA" else "RGB")
                pixel = preview.getpixel((300, 180))
                for channel in pixel[:3]:
                    self.assertAlmostEqual(channel, expected[0], delta=3)
                if mode == "LA":
                    self.assertEqual(pixel[3], 255)
                    self.assertEqual(preview.getpixel((5, 180))[3], 0)

    def test_previews_that_would_change_the_colours_are_refused(self):
        flat = Image.new("L", (800, 600), 128)
        lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        deep = flat.convert("I").point(lambda value: value * 257).convert("I;16")
        cases = [
            ("lab.png", flat, "PNG", {"icc_profile": lab}, "Lab ICC profile on a L image"),
            ("rgb-on-grey.png", flat, "PNG", {"icc_profile": SRGB}, "RGB ICC profile on a L image"),
            ("grey-on-rgb.png", flat.convert("RGB"), "PNG", {"icc_profile": grey_profile(2.2)}, "GRAY ICC profile on a RGB image"),
            ("broken-profile.png", flat, "PNG", {"icc_profile": b"not a profile" * 10}, "unreadable ICC profile"),
            ("print.jpg", Image.new("CMYK", (800, 600), (0, 255, 255, 0)), "JPEG", {}, "unsupported image mode CMYK"),
            ("deep-cutout.png", deep, "PNG", {"transparency": 0}, "unsupported image mode I;16"),
        ]
        for name, image, format_name, options, message in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, re.escape(message)):
                self.render(name, image, format_name, **options)
        # The build stops too, instead of writing a preview with wrong colours.
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Cannot preview"):
            thumbs.build(self.root)


class CheckTests(Fixture):
    def setUp(self):
        super().setUp()
        thumbs.build(self.root)
        self.assertEqual(thumbs.problems(self.root), [])

    def rewrite_entry(self, name, data, **fields):
        """Replace a preview and record its new hash (and any other fields), so only the deeper checks can catch it."""
        manifest = self.manifest()
        entry = manifest["thumbnails"][self.sha(name)]
        (self.root / entry["path"]).write_bytes(data)
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), **fields)
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def webp(self, size, quality=75, format_name="WEBP", **options):
        buffer = io.BytesIO()
        pattern(size, "RGB").save(buffer, format_name, quality=quality, **options)
        return buffer.getvalue()

    def assert_problem(self, pattern_text, reencode=False):
        found = thumbs.problems(self.root, reencode=reencode)
        self.assertTrue(any(pattern_text in problem for problem in found), found)

    def test_corrupted_preview_is_detected(self):
        path = self.root / self.manifest()["thumbnails"][self.sha("wide.png")]["path"]
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 1
        path.write_bytes(raw)
        self.assert_problem("SHA-256")

    def test_missing_preview_is_detected(self):
        (self.root / self.manifest()["thumbnails"][self.sha("wide.png")]["path"]).unlink()
        self.assert_problem("Missing thumbnail file")

    def test_new_original_without_preview_is_detected(self):
        self.add_image("new.png", (900, 900), "RGB", "PNG")
        self.write_manifest()
        self.assert_problem("No thumbnail for assets/public/artists/new.png")

    def test_orphaned_preview_and_stray_file_are_detected(self):
        (self.root / thumbs.THUMB_DIR / "stray.webp.tmp").write_bytes(b"partial")
        self.assert_problem("Unreferenced file in assets/thumbs: assets/thumbs/stray.webp.tmp")
        self.write_manifest([record for record in self.records if not record["path"].endswith("/wide.png")])
        self.assert_problem("belongs to no current gallery original")

    def test_undecodable_preview_is_detected(self):
        self.rewrite_entry("wide.png", b"RIFF\x00\x00\x00\x00WEBPVP8 broken")
        self.assert_problem("does not decode")

    def test_wrong_dimensions_are_detected(self):
        self.rewrite_entry("wide.png", self.webp((400, 240)))
        self.assert_problem(f"{thumbs.MANIFEST} says 480x288")

    def test_preview_of_the_wrong_size_for_its_original_is_detected(self):
        # The file and data/thumbs.json agree on 400x240, but a 1000x600 original needs a 480x288 preview.
        self.rewrite_entry("wide.png", self.webp((400, 240)), width=400, height=240)
        found = thumbs.problems(self.root)
        self.assertFalse(any(" says " in problem for problem in found), found)
        self.assert_problem("is 400x240, expected 480x288 for assets/public/artists/wide.png")

    def test_preview_in_another_format_is_detected(self):
        self.rewrite_entry("wide.png", self.webp((480, 288), format_name="PNG"))
        self.assert_problem("is PNG, not WEBP")

    def test_preview_with_exif_or_xmp_is_detected(self):
        exif = Image.Exif()
        exif[0x010E] = "leaked description"
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
        for options in ({"exif": exif.tobytes()}, {"xmp": xmp}):
            with self.subTest(metadata=next(iter(options))):
                # Same size as the real preview, valid WebP, hash recorded: only the metadata check can catch it.
                self.rewrite_entry("wide.png", self.webp((480, 288), **options))
                found = thumbs.problems(self.root)
                self.assertEqual(found, [f"Thumbnail carries EXIF or XMP metadata: {thumbs.thumb_path(self.sha('wide.png'))}"])

    def test_changed_encoder_settings_are_detected(self):
        manifest = self.manifest()
        manifest["encoder"]["long_edge"] = 512
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        self.assert_problem("Encoder settings changed")

    def test_verify_reencodes_and_catches_foreign_bytes(self):
        self.assertEqual(thumbs.problems(self.root, reencode=True), [])
        # Same size, valid WebP, recorded hash: only re-encoding from the original can tell it apart.
        self.rewrite_entry("wide.png", self.webp((480, 288), quality=95))
        self.assertEqual(thumbs.problems(self.root), [])
        self.assert_problem("does not reproduce", reencode=True)

    def test_command_line_check_fails_with_a_message(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            thumbs.main(["--check"], root=self.root)
        self.assertEqual(json.loads(output.getvalue())["thumbnails"], 3)
        (self.root / self.manifest()["thumbnails"][self.sha("photo.jpg")]["path"]).unlink()
        with self.assertRaises(SystemExit) as raised:
            thumbs.main(["--check"], root=self.root)
        self.assertIn("Missing thumbnail file", str(raised.exception.code))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            thumbs.main(["--check", "--prune"], root=self.root)
        self.assertEqual(raised.exception.code, 2)


class PruneTests(Fixture):
    def test_prune_removes_only_previews_whose_original_left_the_gallery(self):
        thumbs.build(self.root)
        gone = self.root / self.manifest()["thumbnails"][self.sha("wide.png")]["path"]
        kept = {name: data for name, data in self.files().items() if name != gone.name}
        self.write_manifest([record for record in self.records if not record["path"].endswith("/wide.png")])
        summary = thumbs.build(self.root)
        self.assertEqual(summary["orphans"], [gone.relative_to(self.root).as_posix()])
        self.assertTrue(gone.exists(), "a plain build must not delete files")
        self.assertNotIn(self.sha("wide.png"), self.manifest()["thumbnails"])
        self.assertTrue(any("Unreferenced file" in problem for problem in thumbs.problems(self.root)))
        summary = thumbs.build(self.root, prune=True)
        self.assertEqual(summary["pruned"], [gone.relative_to(self.root).as_posix()])
        self.assertFalse(gone.exists())
        self.assertEqual(self.files(), kept)
        self.assertEqual(thumbs.problems(self.root), [])


class CatalogHookTests(Fixture):
    def catalog(self, verify=False, required=True):
        result = catalog_builder.combine(self.root, verify=True)
        catalog_builder.attach_thumbnails(self.root, result, verify, required)
        return {Path(entry["path"]).name: entry for entry in result["assets"]}

    def test_without_thumbnails_the_catalog_is_unchanged(self):
        for entry in self.catalog(verify=True, required=False).values():
            self.assertNotIn("thumb", entry)

    def test_the_repository_build_requires_the_thumbnail_manifest(self):
        # The default is the repository's build: a missing data/thumbs.json fails instead of dropping every preview.
        with self.assertRaisesRegex(FileNotFoundError, "Missing required input: data/thumbs.json"):
            self.catalog()

    def test_gallery_entries_point_at_their_preview_and_keep_the_original(self):
        thumbs.build(self.root)
        entries = self.catalog(verify=True)
        manifest = self.manifest()["thumbnails"]
        for name in ("wide.png", "cutout.png", "photo.jpg"):
            with self.subTest(name=name):
                entry = entries[name]
                self.assertEqual(entry["thumb"], quote(manifest[entry["sha256"]]["path"], safe="/"))
                self.assertEqual(entry["url"], f"assets/public/artists/{name}")
                self.assertTrue(all("thumb" not in record for record in entry["provenance"]))
        self.assertNotIn("thumb", entries["small.png"])
        self.assertNotIn("thumb", entries["booklet.pdf"])

    def test_missing_or_tampered_preview_fails_the_catalog_build(self):
        thumbs.build(self.root)
        path = self.root / self.manifest()["thumbnails"][self.sha("cutout.png")]["path"]
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
        self.catalog(verify=False)  # same size: only --verify hashes previews
        with self.assertRaisesRegex(ValueError, "Thumbnail SHA-256 mismatch"):
            self.catalog(verify=True)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "build_thumbnails"):
            self.catalog()

    def test_previews_must_live_under_assets_thumbs(self):
        thumbs.build(self.root)
        manifest = self.manifest()
        manifest["thumbnails"][self.sha("wide.png")]["path"] = "assets/public/artists/wide.png"
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "assets/thumbs/"):
            self.catalog()


def function_body(source, name):
    """The body of `function name(...) { ... }` in a script, found by matching braces."""
    start = re.search(rf"\bfunction {name}\(", source)
    if not start:
        raise AssertionError(f"function {name}() not found")
    opening = source.index("{", start.end())
    depth = 0
    for index in range(opening, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[opening + 1:index]
    raise AssertionError(f"function {name}() is not closed")


class ArchiveScriptTests(unittest.TestCase):
    """src/archive.js uses previews for grid cards only; the viewer, download and original links serve the original.
    The browser smoke test loads the page; this offline guard fails as soon as the script stops using previews."""

    @classmethod
    def setUpClass(cls):
        source = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        cls.cards, cls.viewer = function_body(source, "more"), function_body(source, "open")

    def test_grid_cards_load_the_preview_when_there_is_one(self):
        self.assertRegex(self.cards, r"\bimage\.src\s*=\s*asset\.thumb\s*\|\|\s*asset\.url\s*;")

    def test_a_failed_preview_falls_back_to_the_original_once(self):
        self.assertRegex(self.cards, r"\bimage\.addEventListener\(\s*[\"']error[\"']\s*,\s*\(\)\s*=>\s*\{?\s*"
                                     r"image\.src\s*=\s*asset\.url\s*;?\s*\}?\s*,\s*\{\s*once\s*:\s*true\s*\}\s*\)")

    def test_viewer_download_and_original_links_use_the_original(self):
        for target in ('$("full-image").src', '$("download").href', '$("original").href'):
            with self.subTest(target=target):
                self.assertRegex(self.viewer, re.escape(target) + r"\s*=\s*asset\.url\s*;")
        self.assertNotIn("thumb", self.viewer)


class RepositoryTests(unittest.TestCase):
    """The committed previews, data/thumbs.json and the generated catalog agree with the archived originals."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / thumbs.MANIFEST).read_text(encoding="utf-8"))["thumbnails"]
        catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
        cls.gallery = [asset for asset in catalog["assets"] if asset["gallery"]]

    def test_committed_thumbnails_are_current(self):
        self.assertEqual(thumbs.problems(ROOT), [])

    def test_catalog_grid_entries_use_the_committed_previews(self):
        wrong = [asset["id"] for asset in self.gallery if asset.get("thumb") != (
            quote(self.manifest[asset["sha256"]]["path"], safe="/") if asset["sha256"] in self.manifest else None)]
        self.assertEqual(wrong, [], f"{len(wrong)} catalog entries disagree with {thumbs.MANIFEST}: run scripts/build_catalog.py")

    def test_first_page_of_the_grid_stays_light(self):
        # archive.js renders 60 cards per page in catalog order by default; before previews this was about 82 MB.
        first_page = self.gallery[:60]
        grid_bytes = sum(self.manifest[asset["sha256"]]["bytes"] if asset["sha256"] in self.manifest else asset["bytes"] for asset in first_page)
        self.assertLess(grid_bytes, 4_000_000)


if __name__ == "__main__":
    unittest.main()
