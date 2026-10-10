"""Checks for catalog integrity, attribution, and safe generated markup."""

import contextlib
import copy
import hashlib
import html
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zlib
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location("build_catalog", Path(__file__).resolve().parents[1] / "scripts/build_catalog.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def png_chunk(data, kind):
    """Offset and data length of the first chunk of this type in a PNG file."""
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        if data[offset + 4:offset + 8] == kind:
            return offset, length
        offset += 12 + length
    raise ValueError(f"No {kind} chunk")


class ImgTags(HTMLParser):
    """The attributes of every <img>, as parsed (names and character references resolved), in order."""

    def __init__(self, markup):
        super().__init__()
        self.images = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(attrs)


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

    def stored(self, relative, raw, **fields):
        """Write bytes under the root; returns the sample record pointed at them, with their size and SHA-256."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {**self.asset, "path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), **fields}

    def template(self):
        (self.root / "templates").mkdir(exist_ok=True)
        template = self.root / "templates/slideshow.html"
        if not template.exists():
            template.write_text("<main>\n@python-work-area\n</main>\n", encoding="utf-8")

    def slides(self, catalog):
        """Each rendered slide's <img> attributes as a dict."""
        self.template()
        return [dict(attrs) for attrs in ImgTags(build.render_slideshow(self.root, catalog)).images]

    def run_main(self, *args):
        """Run the command line against this test's root; returns the SystemExit message, or None on success."""
        self.template()
        with patch.object(build, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            try:
                build.main(list(args))
            except SystemExit as error:
                return str(error)
        return None

    def legacy_pair(self, key="magnolia"):
        # A colour from the key keeps each texture's bytes distinct, so the catalog does not merge them.
        colour = tuple(hashlib.sha256(key.encode()).digest()[:3])
        for variant in ("trans", "tiny"):
            path = self.root / f"assets/legacy/{variant}/{key}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (4, 4), colour)
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

    def test_recorded_metadata_must_match_the_file(self):
        for message, change in {
            "Byte count mismatch": {"bytes": self.asset["bytes"] + 1},
            "Invalid SHA-256": {"sha256": self.asset["sha256"][:40]},
            "Format mismatch": {"format": "JPEG"},
            "Invalid PDF header": {"format": "PDF"},
        }.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                build.validate_asset(self.root, {**self.asset, **change}, True)
        for key in ("id", "source_id", "title", "kind", "page_url", "path", "bytes", "sha256"):
            record = {name: value for name, value in self.asset.items() if name != key}
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, f"Missing {key}"):
                build.validate_asset(self.root, record, True)

    def test_corrupt_image_data_fails_verification(self):
        raw = (self.root / self.asset["path"]).read_bytes()
        offset, length = png_chunk(raw, b"IDAT")
        crc = slice(offset + 8 + length, offset + 12 + length)
        # A wrong checksum, which only Image.verify() notices...
        bad_checksum = bytearray(raw)
        bad_checksum[crc.start] ^= 0xFF
        # ...and a damaged pixel stream under a valid checksum, which only decoding (Image.load()) notices.
        bad_stream = bytearray(raw)
        bad_stream[offset + 8 + length // 2] ^= 0xFF
        bad_stream[crc] = struct.pack(">I", zlib.crc32(bytes(bad_stream[offset + 4:crc.start])))
        for name, data in (("checksum", bad_checksum), ("stream", bad_stream)):
            record = self.stored(f"assets/public/artists/{name}.png", bytes(data))
            with self.subTest(name), self.assertRaises((OSError, SyntaxError)):
                build.validate_asset(self.root, record, True)

    def test_pdf_records_need_a_pdf_header_and_stay_out_of_the_gallery(self):
        pdf = self.stored("assets/public/archives/booklet.pdf", b"%PDF-1.4\n%%EOF\n", id="archive:booklet", source_id="archive",
                          kind="reference", format="PDF", width=None, height=None)
        self.manifest("archives", pdf)
        catalog = build.combine(self.root, verify=True)
        self.assertFalse(catalog["assets"][0]["gallery"])
        self.assertEqual(catalog["summary"]["gallery_images"], 0)
        self.assertEqual(self.slides(catalog), [])
        fake = self.stored("assets/public/archives/fake.pdf", b"<html>not a PDF</html>", format="PDF", width=None, height=None)
        with self.assertRaisesRegex(ValueError, "Invalid PDF header"):
            build.validate_asset(self.root, fake, True)

    def test_reference_images_are_catalogued_but_not_slides(self):
        self.manifest("artists", {**self.asset, "kind": "reference"})
        catalog = build.combine(self.root, verify=True)
        self.assertTrue(catalog["assets"][0]["gallery"])
        self.assertEqual(self.slides(catalog), [])

    def test_manifest_ids_and_sources_must_be_unique_and_declared(self):
        self.manifest("artists", self.asset)
        self.manifest("wikis", {**self.asset, "source_id": "wiki"})
        with self.assertRaisesRegex(ValueError, "Duplicate asset ID: artist:sample"):
            build.combine(self.root, verify=True)
        self.required_inputs()
        (self.root / "data/sources/artists.json").write_text(json.dumps({"sources": [], "assets": [self.asset]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing source: artist"):
            build.combine(self.root, verify=True)
        self.required_inputs()
        self.manifest("artists", self.asset)
        (self.root / "data/sources/wikis.json").write_text(json.dumps({"sources": [{"id": "artist", "name": "Other"}], "assets": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate source IDs"):
            build.combine(self.root, verify=True)

    def test_check_reports_each_stale_or_missing_output_without_writing(self):
        self.manifest("artists", self.asset)
        self.assertIsNone(self.run_main("--verify"))
        self.assertIsNone(self.run_main("--verify", "--check"))
        for relative in ("data/catalog.json", "data/catalog.js", "index.html"):
            with self.subTest(relative):
                path = self.root / relative
                built = path.read_bytes()
                path.write_bytes(built + b"<!-- edited -->")
                self.assertEqual(self.run_main("--check"), f"Out-of-date generated file: {relative}")
                self.assertEqual(path.read_bytes(), built + b"<!-- edited -->")
                path.unlink()
                self.assertEqual(self.run_main("--check"), f"Out-of-date generated file: {relative}")
                self.assertFalse(path.exists())
                path.write_bytes(built)
        self.manifest("artists", {**self.asset, "title": "Renamed upstream"})
        self.assertEqual(self.run_main("--check"), "Out-of-date generated file: data/catalog.json")

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
        self.assertEqual(asset["title_status"], "mapped_exact_internal_key")

    def test_legacy_key_falls_back_to_a_unique_case_insensitive_mapping(self):
        for key in ("Randall", "magnolia", "Echo"):
            self.legacy_pair(key)
        songs = {
            "randall": {"name": "Randall", "artist": "Hikoshi Hashimoto", "book": 0},
            "magnolia": {"name": "Magnolia", "artist": "M2U", "book": 0},
            "Magnolia": {"name": "Not this one", "artist": "Nobody", "book": 0},
            "echo": {"name": "Echo one"}, "ECHO": {"name": "Echo two"},
        }
        mapping = {"source_url": "https://example.com/mapping", "data": {"songs": songs, "books": [{"name": "Collaboration collection"}]}}
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
        assets = {asset["internal_key"]: asset for asset in build.legacy_assets(self.root)}
        randall = assets["Randall"]
        self.assertEqual((randall["title"], randall["composer"], randall["collection"], randall["kind"]),
                         ("Randall", "Hikoshi Hashimoto", "Collaboration collection", "song_art"))
        self.assertEqual(randall["song_titles"], ["Randall"])
        self.assertEqual(randall["title_status"], "mapped_case_insensitive_internal_key")
        self.assertEqual(randall["mapping_source"], "https://example.com/mapping")
        # An exact key always wins over a case variant.
        self.assertEqual((assets["magnolia"]["title"], assets["magnolia"]["title_status"]), ("Magnolia", "mapped_exact_internal_key"))
        # Two keys that differ only in case leave the texture unmapped rather than guessing.
        self.assertEqual((assets["Echo"]["title"], assets["Echo"]["title_status"], assets["Echo"]["mapping_source"]), ("Echo", "internal_key", None))

    def test_unambiguous_legacy_cover_keys_are_collection_covers(self):
        for key in ("deemo1a", "Deemo1B", "booksprites_0", "MN2_booksprites", "deemo1", "deemo1ab", "walkingbythesea"):
            self.legacy_pair(key)
        kinds = {asset["internal_key"]: asset["kind"] for asset in build.legacy_assets(self.root)}
        self.assertEqual(kinds, {
            "deemo1a": "collection_cover", "Deemo1B": "collection_cover", "booksprites_0": "collection_cover",
            "MN2_booksprites": "collection_cover", "deemo1": "illustration", "deemo1ab": "illustration",
            "walkingbythesea": "illustration",
        })

    def test_slideshow_names_no_kind_for_an_unmapped_legacy_texture(self):
        # "illustration" is only the archive's "Illustration / unmapped" catch-all for these; the slideshow eyebrow
        # must not assert it. Mapped textures and cover keys keep their kind.
        for key in ("walkingbythesea", "magnolia", "booksprites_0"):
            self.legacy_pair(key)
        mapping = {"source_url": "https://example.com/mapping", "data": {"songs": {"magnolia": {"name": "Magnolia"}}, "books": []}}
        (self.root / "data/sources/song-mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
        catalog = build.combine(self.root, verify=True)
        self.assertEqual({asset["internal_key"]: asset["kind"] for asset in catalog["assets"]},
                         {"walkingbythesea": "illustration", "magnolia": "song_art", "booksprites_0": "collection_cover"})
        kinds = {slide["data-id"]: slide["data-kind"] for slide in self.slides(catalog)}
        self.assertEqual(kinds, {"legacy:walkingbythesea": "unmapped", "legacy:magnolia": "song_art", "legacy:booksprites_0": "collection_cover"})

    def test_slides_carry_their_mapped_song_titles(self):
        # The liner notes headline the song when a wiki title is only a file key ("Classic01" for "Tristesse").
        self.manifest("artists", {**self.asset, "title": "Classic01", "song_titles": ["Tristesse"]})
        self.manifest("wikis", {**self.asset, "id": "wiki:sample", "source_id": "wiki", "song_titles": ["tristesse", "Ballade No.1"]})
        path = self.root / "assets/public/artists/other.png"
        Image.new("RGB", (8, 12), "black").save(path)
        raw = path.read_bytes()
        self.manifest("archives", {**self.asset, "id": "archive:other", "source_id": "archive", "path": path.relative_to(self.root).as_posix(),
                                   "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        slides = {slide["data-id"]: slide for slide in self.slides(build.combine(self.root, verify=True))}
        self.assertEqual(slides["artist:sample"]["data-songs"], "Tristesse\nBallade No.1")
        self.assertNotIn("data-songs", slides["archive:other"])

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

    def test_symlinks_cannot_lead_an_asset_path_out_of_assets(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "secret.png"
        secret.write_bytes((self.root / self.asset["path"]).read_bytes())
        (self.root / "NOTICE").write_text("not an asset", encoding="utf-8")
        links = {
            "assets/public/artists/outside.png": secret,
            "assets/public/artists/notice.png": self.root / "NOTICE",
            "assets/public/linked": self.root / "data",
        }
        for link, target in links.items():
            try:
                (self.root / link).symlink_to(target)
            except OSError as error:  # Windows without the symlink privilege
                self.skipTest(f"Cannot create symlinks: {error}")
        for value in ("assets/public/artists/outside.png", "assets/public/artists/notice.png", "assets/public/linked/sources/artists.json"):
            with self.subTest(value), self.assertRaisesRegex(ValueError, "outside"):
                build.safe_path(self.root, value)
        with self.assertRaisesRegex(ValueError, "outside"):
            build.validate_asset(self.root, {**self.asset, "path": "assets/public/artists/outside.png"}, True)
        # A checkout whose whole assets/ directory links elsewhere does not pass either.
        checkout = Path(outside.name) / "checkout"
        checkout.mkdir()
        (checkout / "assets").symlink_to(self.root / "assets", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside"):
            build.safe_path(checkout, self.asset["path"])

    def test_unsafe_paths_rejected(self):
        for path in ("../secret.png", "/tmp/secret.png", "assets/../../secret.png", "README.md", "trans/magnolia.png", "tiny/magnolia.png", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build.safe_path(self.root, path)

    def test_source_urls_are_web_pages(self):
        with self.assertRaisesRegex(ValueError, "Unsafe source URL"):
            build.validate_asset(self.root, {**self.asset, "page_url": "javascript:alert(1)"})

    def test_html_escapes_remote_metadata(self):
        # Scraped titles and names go into double-quoted attributes: none may end the attribute or the tag, add an
        # attribute, or lose a character (a raw CR would be read back as LF by an HTML parser).
        hostile = 'A & B "><script>alert(1)</script>\' onerror=\'alert(2)\r\nline 2\tend <b>&amp;'
        record = {**self.asset, "title": hostile, "composer": f"Composer {hostile}", "artist": f"Artist {hostile}",
                  "collection": f"Pack {hostile}", "song_titles": [f"Song {hostile}"]}
        asset = {**copy.deepcopy(record), "family": "artists", "gallery": True, "url": "assets/public/artists/sample.png",
                 "source_name": f"Source {hostile}", "provenance": [record]}
        self.template()
        rendered = build.render_slideshow(self.root, {"assets": [asset]})
        images = ImgTags(rendered).images
        self.assertEqual(len(images), 1)
        self.assertEqual([name for name, _ in images[0]], [
            "class", "data-src", "data-id", "data-title", "data-source", "data-source-id", "data-page", "data-size",
            "data-kind", "data-songs", "data-composer", "data-artist", "data-collection", "alt",
        ])
        values = dict(images[0])
        self.assertEqual(values["data-title"], hostile)
        self.assertEqual(values["alt"], hostile)
        self.assertEqual(values["data-source"], f"Source {hostile}")
        self.assertEqual(values["data-songs"], f"Song {hostile}")
        self.assertEqual(values["data-composer"], f"Composer {hostile}")
        self.assertEqual(values["data-artist"], f"Artist {hostile}")
        self.assertEqual(values["data-collection"], f"Pack {hostile}")
        self.assertNotIn("<script", rendered)
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\t", rendered)
        self.assertEqual(len(rendered.splitlines()), 3, "the slide must stay on one line")
        # attr() holds in a single-quoted attribute too.
        self.assertEqual(ImgTags(f"<img alt='{build.attr(hostile)}'>").images, [[("alt", hostile)]])

    def test_attributes_escape_control_whitespace(self):
        value = "a\rb\tc\nd"
        self.assertEqual(build.attr(value), "a&#13;b&#9;c&#10;d")
        self.assertEqual(html.unescape(build.attr(value)), value)

    def test_check_passes_right_after_a_build_whose_metadata_has_a_carriage_return(self):
        self.manifest("artists", {**self.asset, "title": "Berlin\rStation Chief"})
        self.assertIsNone(self.run_main())
        self.assertNotIn(b"\r", (self.root / "index.html").read_bytes())
        self.assertIsNone(self.run_main("--check"))

    def test_check_compares_line_ends_exactly_except_a_crlf_checkout(self):
        self.manifest("artists", self.asset)
        self.assertIsNone(self.run_main())
        index = self.root / "index.html"
        built = index.read_bytes()
        self.assertNotIn(b"\r", built)
        # Git with core.autocrlf checks text out with CRLF line ends: still the same generated file.
        index.write_bytes(built.replace(b"\n", b"\r\n"))
        self.assertIsNone(self.run_main("--check"))
        # A lone CR where the build wrote LF is a different file.
        index.write_bytes(built.replace(b"\n", b"\r", 1))
        self.assertEqual(self.run_main("--check"), "Out-of-date generated file: index.html")


if __name__ == "__main__":
    unittest.main()
