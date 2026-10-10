"""Check third-party licence texts, inherited-media provenance and attribution statements."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_INDEXES = ("licenses/README.md", "licenses/README.zh-CN.md")
MIT_GRANT = "Permission is hereby granted, free of charge, to any person"
MIT_DISCLAIMER = 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'
# Components bundled in src/vendor/legacy-ui.js: licence file -> (its copyright line, a marker in the bundle).
VENDOR_LICENSES = {
    "licenses/html2canvas-MIT.txt": ("Copyright (c) 2012 Niklas von Hertzen", b"html2canvas 0.5.0-beta3"),
    "licenses/punycode-MIT.txt": ("Copyright Mathias Bynens", b'version:"1.2.4"'),
    "licenses/pace-MIT.txt": ("Copyright (c) 2013 HubSpot, Inc.", b"/*! pace 1.0.0 */"),
}


def words(text):
    return " ".join(text.split())


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


if __name__ == "__main__":
    unittest.main()
