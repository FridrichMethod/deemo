"""Offline checks of the slideshow template and stylesheet.
Runtime behaviour (hit-testing, focus, layout) is covered by tests/browser_smoke.py."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/slideshow.html"
STYLESHEET = ROOT / "src/slideshow.css"


class SlideshowTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8-sig")
        cls.css = STYLESHEET.read_text(encoding="utf-8")

    def test_screenshot_overlay_does_not_live_in_the_url(self):
        self.assertNotIn(":target", self.css, "The overlay must not depend on the URL fragment")
        self.assertNotRegex(self.html, r"location\.(?:href|hash)\s*=")
        self.assertNotIn('href="#"', self.html)


if __name__ == "__main__":
    unittest.main()
