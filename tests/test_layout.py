"""Check relocated files, unchanged legacy bytes, and subdirectory-safe links."""

import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CSS_URL = re.compile(r'''url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)''', re.IGNORECASE)
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".mp3", ".ttf", ".woff", ".woff2"}


def css_urls(text):
    return [next(group for group in match.groups() if group is not None).strip() for match in CSS_URL.finditer(text)]


class ResourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.urls.extend(attributes[key] for key in ("src", "href", "data-src", "poster") if attributes.get(key))
        if tag == "meta" and attributes.get("property") == "og:image":
            self.urls.append(attributes["content"])
        self.urls.extend(css_urls(attributes.get("style", "")))
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.urls.extend(css_urls(data))


class LayoutTests(unittest.TestCase):
    def assert_local_resource(self, page, url):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return
        self.assertFalse(parsed.path.startswith("/"), f"Root-relative URL breaks nested hosting: {page}: {url}")
        target = (page.parent / unquote(parsed.path)).resolve()
        self.assertTrue(target.is_relative_to(ROOT), f"URL escapes repository: {page}: {url}")
        self.assertTrue(target.is_file(), f"Missing local resource: {page.relative_to(ROOT)}: {url}")

    def test_entry_pages_and_template_reference_existing_local_resources(self):
        for relative in ("archive.html", "index.html", "templates/slideshow.html"):
            with self.subTest(page=relative):
                page = ROOT / relative
                parser = ResourceLinks()
                parser.feed(page.read_text(encoding="utf-8-sig"))
                # The template is rendered at the repository root as index.html.
                served_page = ROOT / "index.html" if relative.startswith("templates/") else page
                for url in parser.urls:
                    self.assert_local_resource(served_page, url)

    def test_css_resources_resolve_relative_to_stylesheets(self):
        sheets = sorted((ROOT / "src").rglob("*.css"))
        self.assertIn(ROOT / "src/slideshow.css", sheets)
        for sheet in sheets:
            for url in css_urls(sheet.read_text(encoding="utf-8-sig")):
                with self.subTest(stylesheet=sheet.relative_to(ROOT), url=url):
                    self.assert_local_resource(sheet, url)

    def test_css_url_parser_preserves_spaces_and_optional_quotes(self):
        self.assertEqual(
            css_urls('src: url("../assets/a b.ttf"); src: url(../assets/c d.ttf); background: url(\'../assets/e f.png\');'),
            ["../assets/a b.ttf", "../assets/c d.ttf", "../assets/e f.png"],
        )

    def test_manifest_and_browserconfig_icons_exist(self):
        manifest_path = ROOT / "site.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assert_local_resource(manifest_path, manifest["start_url"])
        self.assertTrue(manifest["icons"])
        for icon in manifest["icons"]:
            self.assertTrue(icon["src"].removeprefix("./").startswith("assets/site/icons/"))
            self.assert_local_resource(manifest_path, icon["src"])
        browserconfig_path = ROOT / "browserconfig.xml"
        icons = [element.attrib["src"] for element in ET.parse(browserconfig_path).iter() if "src" in element.attrib]
        self.assertTrue(icons)
        for url in icons:
            self.assertTrue(url.removeprefix("./").startswith("assets/site/icons/"))
            self.assert_local_resource(browserconfig_path, url)

    def test_mapping_points_to_preserved_license(self):
        mapping = json.loads((ROOT / "data/sources/song-mapping.json").read_text(encoding="utf-8"))
        self.assertEqual(mapping["license_path"], "licenses/DeemoSongs-MIT.txt")
        self.assertTrue((ROOT / mapping["license_path"]).is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "NOTICE").is_file())

    def test_legacy_inventory_covers_relocated_files_without_changing_bytes(self):
        inventory = json.loads((ROOT / "data/legacy-inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(inventory["schema_version"], 1)
        self.assertRegex(inventory["source_commit"], r"^[0-9a-f]{40}$")
        records = inventory["files"]
        self.assertTrue(records)
        self.assertEqual(len({record["original_path"] for record in records}), len(records))
        targets = {record["path"] for record in records}
        self.assertEqual(len(targets), len(records))
        required = {"LICENSE", "licenses/DeemoSongs-MIT.txt", "src/vendor/legacy-ui.js"}
        for directory in ("assets/legacy", "assets/site"):
            required.update(path.relative_to(ROOT).as_posix() for path in (ROOT / directory).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        # The README was added during migration; only the scripts are inherited.
        required.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "scripts/legacy").glob("*.py"))
        self.assertTrue(required.issubset(targets), f"Files absent from preserved-byte inventory: {sorted(required - targets)}")
        for record in records:
            with self.subTest(path=record["path"]):
                path = (ROOT / record["path"]).resolve()
                self.assertTrue(path.is_relative_to(ROOT))
                raw = path.read_bytes()
                self.assertEqual(len(raw), record["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"])
                self.assertEqual(hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(), record["git_blob"])

    def test_root_and_src_have_no_unmigrated_media(self):
        for relative in ("tiny", "trans", "pil", "html.py", "test.html", "style.css", "src/main.js", "docs/licenses"):
            with self.subTest(path=relative):
                self.assertFalse((ROOT / relative).exists())
        for path in list(ROOT.iterdir()) + list((ROOT / "src").rglob("*")):
            if path.is_file():
                self.assertNotIn(path.suffix.lower(), MEDIA_SUFFIXES, str(path.relative_to(ROOT)))

    def test_layout_has_no_case_insensitive_filename_collisions(self):
        seen = {}
        paths = [path for directory in ("assets", "src", "scripts", "templates", "licenses") for path in (ROOT / directory).rglob("*") if path.is_file()]
        paths.extend(path for path in ROOT.iterdir() if path.is_file())
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            self.assertNotIn(relative.casefold(), seen, f"Case-insensitive collision: {relative} / {seen.get(relative.casefold())}")
            seen[relative.casefold()] = relative


if __name__ == "__main__":
    unittest.main()
