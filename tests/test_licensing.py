"""Check third-party licence texts, inherited-media provenance and attribution statements."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data/legacy-inventory.json"
LICENSE_INDEXES = ("licenses/README.md", "licenses/README.zh-CN.md")
MIT_GRANT = "Permission is hereby granted, free of charge, to any person"
MIT_DISCLAIMER = 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'
# Components bundled in src/vendor/legacy-ui.js: licence file -> (its copyright line, a marker in the bundle).
VENDOR_LICENSES = {
    "licenses/html2canvas-MIT.txt": ("Copyright (c) 2012 Niklas von Hertzen", b"html2canvas 0.5.0-beta3"),
    "licenses/punycode-MIT.txt": ("Copyright Mathias Bynens", b'version:"1.2.4"'),
    "licenses/pace-MIT.txt": ("Copyright (c) 2013 HubSpot, Inc.", b"/*! pace 1.0.0 */"),
}
# Commercial fonts inherited from upstream whose licences forbid redistribution (see NOTICE).
REMOVED_FONTS = {"COPRGTL.ttf", "RocknRoll_Typo_bold.ttf"}
# CSS family names the slideshow still uses; the removed faces resolve only to fonts installed locally.
LOCAL_ONLY_FAMILIES = {"Coprhtl", "COPRGTL", "RocknRoll"}
FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}")
DECLARATION = re.compile(r"([\w-]+)\s*:\s*([^;]+);")
FONT_URL = re.compile(r"""url\(\s*["']?([^"')]+\.(?:ttf|otf|woff2?))["']?\s*\)""", re.IGNORECASE)
# Upstream (mashirozx/deemo) files changed here, Apache-2.0 section 4(b): current path -> upstream name.
MODIFIED_UPSTREAM = {
    "templates/slideshow.html": "test.html",
    "src/slideshow.css": "style.css",
    "browserconfig.xml": "browserconfig.xml",
}
# Changed upstream files that cannot carry a comment, or that were replaced outright; NOTICE names them.
NOTED_IN_NOTICE = ("index.html", "site.webmanifest", ".gitattributes", "README.md", "html.py", "scripts/build_catalog.py")


def words(text):
    return " ".join(text.split())


def inventory_records():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))["files"]


class VendorLicenseTests(unittest.TestCase):
    def test_bundled_components_ship_their_mit_texts(self):
        bundle = (ROOT / "src/vendor/legacy-ui.js").read_bytes()
        for relative, (copyright_line, marker) in VENDOR_LICENSES.items():
            with self.subTest(license=relative):
                self.assertIn(marker, bundle, "The bundle no longer contains this component; update the table")
                path = ROOT / relative
                self.assertTrue(path.is_file(), f"Missing MIT licence text: {relative}")
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(copyright_line))
                self.assertIn(MIT_GRANT, words(text))
                self.assertIn(MIT_DISCLAIMER, words(text))

    def test_licence_texts_are_named_in_the_index_and_notice(self):
        for document in (*LICENSE_INDEXES, "NOTICE"):
            text = (ROOT / document).read_text(encoding="utf-8")
            for relative in VENDOR_LICENSES:
                with self.subTest(document=document, license=relative):
                    self.assertIn(Path(relative).name, text)
            with self.subTest(document=document):
                for component in ("html2canvas 0.5.0-beta3", "punycode.js 1.2.4", "Pace 1.0.0"):
                    self.assertIn(component, text)


class FontTests(unittest.TestCase):
    def test_commercial_fonts_are_not_redistributed(self):
        shipped = {path.name for path in (ROOT / "assets").rglob("*") if path.is_file()}
        self.assertEqual(shipped & REMOVED_FONTS, set())
        self.assertEqual({Path(record["path"]).name for record in inventory_records()} & REMOVED_FONTS, set())

    def test_removed_faces_use_only_locally_installed_fonts(self):
        faces = {}
        for sheet in sorted((ROOT / "src").rglob("*.css")):
            for body in FONT_FACE.findall(sheet.read_text(encoding="utf-8-sig")):
                rules = dict(DECLARATION.findall(body))
                faces[rules["font-family"].strip().strip("\"'")] = rules["src"]
        self.assertTrue(LOCAL_ONLY_FAMILIES <= set(faces), "The slideshow's font family names must stay defined")
        for family in LOCAL_ONLY_FAMILIES:
            with self.subTest(family=family):
                self.assertNotIn("url(", faces[family])
                self.assertIn("local(", faces[family])

    def test_pages_and_stylesheets_reference_only_shipped_font_files(self):
        sources = [*sorted((ROOT / "src").rglob("*.css")), ROOT / "templates/slideshow.html", ROOT / "archive.html"]
        for source in sources:
            # The template is served from the repository root as index.html.
            base = ROOT if source.parent.name == "templates" else source.parent
            for url in FONT_URL.findall(source.read_text(encoding="utf-8-sig")):
                with self.subTest(source=source.relative_to(ROOT).as_posix(), url=url):
                    self.assertNotIn(Path(url).name, REMOVED_FONTS)
                    self.assertTrue((base / url).resolve().is_file())


class ProvenanceTests(unittest.TestCase):
    def test_every_inherited_site_file_has_a_provenance_row(self):
        inherited = {record["path"]: record["original_path"] for record in inventory_records()}
        files = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "assets/site").rglob("*") if path.is_file())
        self.assertTrue(files)
        for document in LICENSE_INDEXES:
            rows = [line for line in (ROOT / document).read_text(encoding="utf-8").splitlines() if line.startswith("|")]
            for relative in files:
                with self.subTest(document=document, file=relative):
                    matching = [row for row in rows if row.startswith(f"| `{relative.removeprefix('assets/site/')}` |")]
                    self.assertEqual(len(matching), 1, "Each file under assets/site/ needs exactly one provenance row")
                    self.assertIn(f"`{inherited.get(relative, '(not in the legacy inventory)')}`", matching[0], "Name the inherited upstream path")

    def test_readmes_state_the_inventory_size(self):
        count = len(inventory_records())
        for document, pattern in (("README.md", r"SHA-256 of (\d+) legacy files"), ("README.zh-CN.md", r"(\d+) 个旧文件")):
            with self.subTest(document=document):
                match = re.search(pattern, (ROOT / document).read_text(encoding="utf-8"))
                self.assertIsNotNone(match)
                self.assertEqual(int(match.group(1)), count)


class AttributionTests(unittest.TestCase):
    def test_slideshow_header_matches_the_notice_rights_statement(self):
        template = (ROOT / "templates/slideshow.html").read_text(encoding="utf-8-sig")
        self.assertTrue(template.startswith("<!--"))
        header = words(template[: template.index("-->")])
        self.assertNotIn("all assets belongs to Rayark", header)
        self.assertNotIn("所有素材版权为雷亞遊戲（Rayark Inc.）所有", header)
        self.assertIn("Rayark Inc. and the respective creators", header)
        self.assertIn("雷亞遊戲（Rayark Inc.）与各自的创作者", header)
        self.assertIn("NOTICE", header)

    def test_modified_upstream_files_carry_change_notices(self):
        for relative, upstream in MODIFIED_UPSTREAM.items():
            with self.subTest(file=relative):
                head = words((ROOT / relative).read_text(encoding="utf-8-sig")[:1500])
                self.assertIn(f"Derived from mashirozx/deemo {upstream} (Apache-2.0", head)
                self.assertIn("modified by FridrichMethod, 2026", head)

    def test_notice_lists_every_modified_upstream_file(self):
        notice = words((ROOT / "NOTICE").read_text(encoding="utf-8"))
        for relative in (*MODIFIED_UPSTREAM, *NOTED_IN_NOTICE):
            with self.subTest(file=relative):
                self.assertIn(relative, notice)


if __name__ == "__main__":
    unittest.main()
