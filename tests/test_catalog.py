"""Checks for catalog integrity, attribution, and safe generated markup."""

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location("build_catalog", Path(__file__).resolve().parents[1] / "scripts/build_catalog.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data/sources").mkdir(parents=True)
        path = self.root / "assets/public/artists/sample.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (8, 12), "white").save(path)
        raw = path.read_bytes()
        self.asset = {
            "id": "artist:sample", "source_id": "artist", "title": "Sample",
            "kind": "song_art", "page_url": "https://example.com/art",
            "path": "assets/public/artists/sample.png", "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "width": 8, "height": 12,
            "format": "PNG", "game": "DEEMO",
        }
        self.required_inputs()

    def required_inputs(self):
        """Empty versions of every input a build requires; each test then adds only what it exercises."""
        for family in build.SOURCE_MANIFESTS:
            (self.root / f"data/sources/{family}.json").write_text(json.dumps({"sources": [], "assets": []}), encoding="utf-8")
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps({"data": {"songs": {}, "books": []}}), encoding="utf-8")
        for variant in ("trans", "tiny"):
            (self.root / f"assets/legacy/{variant}").mkdir(parents=True, exist_ok=True)

    def manifest(self, family, asset):
        content = {"sources": [{"id": asset["source_id"], "name": family, "url": asset["page_url"]}], "assets": [asset]}
        (self.root / f"data/sources/{family}.json").write_text(json.dumps(content), encoding="utf-8")

    def legacy_pair(self, key="magnolia"):
        for variant in ("trans", "tiny"):
            path = self.root / f"assets/legacy/{variant}/{key}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (4, 4), "white")
            if variant == "tiny":
                image = image.quantize(colors=256)
            image.save(path)

    def test_exact_duplicates_keep_both_provenances(self):
        self.manifest("artists", self.asset)
        duplicate = {**self.asset, "id": "wiki:sample", "source_id": "wiki"}
        self.manifest("wikis", duplicate)
        result = build.combine(self.root, verify=True)
        self.assertEqual(result["summary"]["unique_files"], 1)
        self.assertEqual(len(result["assets"][0]["provenance"]), 2)
        self.assertEqual(result["assets"][0]["family"], "artists")

    def test_same_title_different_bytes_keeps_versions(self):
        self.manifest("artists", self.asset)
        path = self.root / "assets/public/artists/other.png"
        Image.new("RGB", (8, 12), "black").save(path)
        raw = path.read_bytes()
        other = {**self.asset, "id": "wiki:other", "source_id": "wiki", "path": path.relative_to(self.root).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.manifest("wikis", other)
        self.assertEqual(len(build.combine(self.root, verify=True)["assets"]), 2)

    def test_hash_and_dimensions_are_verified(self):
        bad_hash = {**self.asset, "sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            build.validate_asset(self.root, bad_hash, True)
        with self.assertRaisesRegex(ValueError, "Dimension mismatch"):
            build.validate_asset(self.root, {**self.asset, "width": 9}, True)

    def test_legacy_mapping_distinguishes_composer_and_illustrator(self):
        self.legacy_pair()
        mapping = {"source_url": "https://example.com/mapping", "data": {"songs": {"magnolia": {"name": "Magnolia", "artist": "M2U", "book": 0}}, "books": [{"name": "Collection"}]}}
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps(mapping))
        asset = build.legacy_assets(self.root)[0]
        self.assertEqual(asset["title"], "Magnolia")
        self.assertEqual(asset["composer"], "M2U")
        self.assertIsNone(asset["artist"])
        self.assertEqual(asset["collection"], "Collection")
        self.assertEqual(asset["kind"], "song_art")

    def test_quantized_legacy_file_is_a_verified_variant_not_an_extra_slide(self):
        self.legacy_pair()
        catalog = build.combine(self.root, verify=True)
        self.assertEqual(catalog["summary"]["legacy_quantized_files"], 1)
        self.assertEqual(catalog["summary"]["source_asset_records"], 1)
        self.assertEqual(catalog["summary"]["gallery_images"], 1)
        self.assertEqual(catalog["summary"]["unique_files"], 1)
        asset = catalog["assets"][0]
        self.assertEqual(asset["path"], "assets/legacy/trans/magnolia.png")
        self.assertEqual(len(asset["variants"]), 1)
        variant = asset["variants"][0]
        self.assertEqual(variant["id"], "legacy:tiny:magnolia")
        self.assertEqual(variant["role"], "palette_quantized")
        self.assertEqual(variant["source_id"], "legacy")
        self.assertEqual(variant["path"], "assets/legacy/tiny/magnolia.png")
        self.assertEqual(variant["url"], "assets/legacy/tiny/magnolia.png")
        self.assertEqual(variant["width"], 4)
        self.assertEqual(variant["height"], 4)
        raw = (self.root / variant["path"]).read_bytes()
        self.assertEqual(variant["bytes"], len(raw))
        self.assertEqual(variant["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(asset["provenance"][0]["variants"], asset["variants"])

    def test_corrupted_quantized_variant_fails_catalog_verification(self):
        self.legacy_pair()
        recorded = build.legacy_assets(self.root)
        path = self.root / recorded[0]["variants"][0]["path"]
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 1
        path.write_bytes(raw)
        # Freeze the previously recorded hashes, then alter only the tiny file.
        # This exercises combine's nested validation, not just validate_asset.
        with patch.object(build, "legacy_assets", return_value=recorded):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                build.combine(self.root, verify=True)

    def test_missing_required_inputs_are_rejected(self):
        # A lost manifest, mapping or legacy directory must not quietly build a smaller site.
        self.assertEqual(build.combine(self.root, verify=True)["summary"]["unique_files"], 0)
        for relative in [f"data/sources/{family}.json" for family in build.SOURCE_MANIFESTS] + [
                "data/sources/song-mapping.json", "assets/legacy/trans", "assets/legacy/tiny"]:
            with self.subTest(missing=relative):
                path = self.root / relative
                path.rmdir() if path.is_dir() else path.unlink()
                with self.assertRaisesRegex(FileNotFoundError, f"Missing required input: {relative}"):
                    build.combine(self.root, verify=True)
                self.required_inputs()

    def test_song_mapping_without_songs_is_rejected(self):
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps({"data": {}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Song mapping"):
            build.legacy_assets(self.root)

    def test_legacy_order_is_by_file_name_on_every_platform(self):
        # WindowsPath compares case-folded parts; a path type that does the same here makes the order come
        # from an explicit file-name key rather than from how the platform's paths happen to compare.
        class CaseFoldedPath(type(self.root)):
            def __lt__(self, other):
                return str(self).casefold() < str(other).casefold()

        for key in ("beta", "Zeta", "alpha"):
            self.legacy_pair(key)
        keys = [asset["internal_key"] for asset in build.legacy_assets(CaseFoldedPath(self.root))]
        self.assertEqual(keys, ["Zeta", "alpha", "beta"])

    def test_missing_quantized_legacy_pair_is_rejected(self):
        self.legacy_pair()
        (self.root / "assets/legacy/tiny/magnolia.png").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            build.legacy_assets(self.root)

    def test_unsafe_paths_rejected(self):
        for path in ("../secret.png", "/tmp/secret.png", "assets/../../secret.png", "README.md", "trans/magnolia.png", "tiny/magnolia.png", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build.safe_path(self.root, path)

    def test_source_urls_are_web_pages(self):
        with self.assertRaisesRegex(ValueError, "Unsafe source URL"):
            build.validate_asset(self.root, {**self.asset, "page_url": "javascript:alert(1)"})

    def test_html_escapes_remote_metadata(self):
        (self.root / "templates").mkdir()
        (self.root / "templates/slideshow.html").write_text("<main>@python-work-area</main>")
        asset = copy.deepcopy(self.asset)
        asset.update(gallery=True, url="assets/public/sample.png", source_name="Artist", title='A & B "><script>alert(1)</script>')
        rendered = build.render_slideshow(self.root, {"assets": [asset]})
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("A &amp; B", rendered)


if __name__ == "__main__":
    unittest.main()
