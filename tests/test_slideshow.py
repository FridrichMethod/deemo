"""Offline checks of the slideshow template and stylesheet: named, keyboard-operable controls in both
languages and a screenshot overlay that does not live in the URL.
Runtime behaviour (hit-testing, focus, layout) is covered by tests/browser_smoke.py."""

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/slideshow.html"
STYLESHEET = ROOT / "src/slideshow.css"
MESSAGES = ROOT / "src/i18n/slideshow.js"
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Icon-only controls, found by their own or their container's class: whether each is a toggle (aria-pressed).
ICON_BUTTONS = {"prev": False, "next": False, "photo": False, "random": True, "iplayer": True, "save": False, "close": False}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, attrs, parent, [], ""

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        self.in_script = tag == "script"
        if tag not in VOID:
            self.current = node

    def handle_endtag(self, tag):
        self.in_script = False
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        if not self.in_script:
            node = self.current
            while node is not None:
                node.text += data
                node = node.parent


def i18n_attrs(node):
    pairs = (pair.split(":", 1) for pair in (node.attrs.get("data-i18n-attr") or "").split(";") if ":" in pair)
    return {attribute.strip(): key.strip() for attribute, key in pairs}


class SlideshowTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8-sig")
        cls.css = STYLESHEET.read_text(encoding="utf-8")
        tree = Tree()
        tree.feed(cls.html)
        cls.nodes = list(tree.root.walk())
        cls.messages = json.loads(REGISTER.search(MESSAGES.read_text(encoding="utf-8")).group(1))

    def control(self, name):
        for node in self.nodes:
            if name in node.classes:
                if node.tag in ("a", "button"):
                    return node
                inner = [child for child in node.walk() if child.tag in ("a", "button")]
                self.assertEqual(len(inner), 1, f".{name} should hold one control")
                return inner[0]
        self.fail(f"No .{name} control in the template")

    def assert_translated(self, key):
        for lang in ("en", "zh-CN"):
            self.assertTrue(self.messages[lang].get(key, "").strip(), f"{key} has no {lang} string in src/i18n/slideshow.js")

    def test_icon_only_controls_are_named_buttons_in_both_languages(self):
        for name, toggle in ICON_BUTTONS.items():
            with self.subTest(control=name):
                node = self.control(name)
                self.assertEqual((node.tag, node.attrs.get("type")), ("button", "button"), "Actions are <button type=button>, not links")
                key = i18n_attrs(node).get("aria-label")
                self.assertTrue(key, "Icon-only controls take an aria-label translated through data-i18n-attr")
                self.assert_translated(key)
                self.assertEqual(node.attrs.get("aria-label"), self.messages["en"][key])
                if toggle:
                    self.assertIn(node.attrs.get("aria-pressed"), ("true", "false"), "Toggles expose their state with aria-pressed")

    def test_no_javascript_urls(self):
        for node in self.nodes:
            with self.subTest(tag=node.tag, href=node.attrs.get("href")):
                self.assertFalse((node.attrs.get("href") or "").strip().lower().startswith("javascript:"))

    def test_template_images_have_alt_text(self):
        for node in self.nodes:
            if node.tag == "img":
                with self.subTest(src=node.attrs.get("src")):
                    self.assertIn("alt", node.attrs, "Decorative icons take alt=\"\"")

    def test_accessible_names_contain_the_visible_label(self):
        """WCAG 2.5.3: an aria-label on a control with visible text must contain that text."""
        for node in self.nodes:
            label_key, text_key = i18n_attrs(node).get("aria-label"), node.attrs.get("data-i18n")
            if label_key and text_key:
                for lang in ("en", "zh-CN"):
                    with self.subTest(control=text_key, lang=lang):
                        self.assertIn(self.messages[lang][text_key].casefold(), self.messages[lang][label_key].casefold())

    def test_screenshot_overlay_does_not_live_in_the_url(self):
        self.assertNotIn(":target", self.css, "The overlay must not depend on the URL fragment")
        self.assertNotRegex(self.html, r"location\.(?:href|hash)\s*=")
        self.assertNotIn('href="#"', self.html)


if __name__ == "__main__":
    unittest.main()
