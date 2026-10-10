"""Derived grid thumbnails: deterministic output, drift detection and pruning."""

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


thumbs = load("build_thumbnails")


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
        buffer = io.BytesIO()
        image = pattern(size, mode)
        if format_name == "JPEG":
            # EXIF must not reach the preview; the colour profile must, so colours match the original.
            exif = Image.Exif()
            exif[0x010E] = "fixture description"
            image.save(buffer, format_name, quality=90, exif=exif, icc_profile=SRGB)
        else:
            image.save(buffer, format_name)
        self.add_file(name, buffer.getvalue(), {"width": size[0], "height": size[1], "format": format_name})

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

    def test_changed_encoder_settings_need_an_explicit_rebuild(self):
        thumbs.build(self.root)
        manifest = self.manifest()
        manifest["encoder"]["quality"] = 1
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--rebuild"):
            thumbs.build(self.root)
        self.assertEqual(thumbs.build(self.root, rebuild=True)["rendered"], 3)
        self.assertEqual(thumbs.problems(self.root), [])


class CheckTests(Fixture):
    def setUp(self):
        super().setUp()
        thumbs.build(self.root)
        self.assertEqual(thumbs.problems(self.root), [])

    def rewrite_entry(self, name, data):
        """Replace a preview and record its new hash, so only the deeper checks can catch it."""
        manifest = self.manifest()
        entry = manifest["thumbnails"][self.sha(name)]
        (self.root / entry["path"]).write_bytes(data)
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        (self.root / thumbs.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")

    def webp(self, size, quality=75):
        buffer = io.BytesIO()
        pattern(size, "RGB").save(buffer, "WEBP", quality=quality)
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
        self.assert_problem("Dimension mismatch")

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


if __name__ == "__main__":
    unittest.main()
