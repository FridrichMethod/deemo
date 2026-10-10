"""Offline guards for the slideshow template and stylesheet: named, keyboard-operable controls in both
languages, a bottom row whose DOM order follows its layout, a screenshot overlay that does not live in the URL,
slides that crossfade without a keyframe fade and take no hits while hidden, an artist credit that no tier
drops, arrows kept below the language toggle on short landscape screens, a signature whose box leaves the
toggle its taps, and the visible attribution link. They read the sources only; real hit-testing, focus,
keyboard handling and layout need a browser (tests/browser_smoke.py)."""

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/slideshow.html"
STYLESHEET = ROOT / "src/slideshow.css"
MESSAGES = ROOT / "src/i18n/slideshow.js"
NOTICE_URL = "https://github.com/FridrichMethod/deemo/blob/main/NOTICE"
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# The round controls of the bottom row, left to right on screen.
BOTTOM_ROW = ["photo", "github", "archive-link", "iplayer", "random"]
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


def top_level_css(css):
    """The stylesheet without its @media / @supports blocks."""
    out, depth, index = [], 0, 0
    for match in re.finditer(r"@(?:media|supports)[^{]*\{|\{|\}", css):
        if depth == 0:
            out.append(css[index:match.start()])
        if match.group().startswith("@"):
            depth += 1
        elif depth and match.group() == "{":
            depth += 1
        elif depth and match.group() == "}":
            depth -= 1
        elif depth == 0:
            out.append(match.group())
        index = match.end()
    out.append(css[index:] if depth == 0 else "")
    return "".join(out)


def media_block(css, condition):
    """The body of the first @media block whose condition contains `condition`."""
    match = re.search(r"@media[^{]*" + re.escape(condition) + r"[^{]*\{", css)
    if not match:
        return None
    depth = 1
    for brace in re.finditer(r"[{}]", css[match.end():]):
        depth += 1 if brace.group() == "{" else -1
        if depth == 0:
            return css[match.end():match.end() + brace.start()]
    return None


def css_value(css, selectors, prop):
    """The last value `css` gives `prop` in a rule for exactly this selector list (one selector per line or not)."""
    rule = r"(?m)^[ \t]*" + r"\s*,\s*".join(map(re.escape, selectors)) + r"\s*\{([^}]*)\}"
    values = [found.group(1) for body in re.findall(rule, css)
              for found in re.finditer(r"(?<![\w-])" + re.escape(prop) + r"\s*:\s*([^;]+?)\s*(?:;|$)", body)]
    return values[-1] if values else None


def px(value):
    """A pixel length (or a bare 0) as a number; None for anything else."""
    match = re.fullmatch(r"(-?[\d.]+)px|(0)", value or "")
    return float(match.group(1) or 0) if match else None


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

    def test_bottom_row_dom_order_follows_its_layout(self):
        order = [cls for node in self.nodes if "bt" in node.classes for cls in node.classes if cls in BOTTOM_ROW]
        self.assertEqual(order, BOTTOM_ROW, "Tab order should run left to right along the control row")
        css = top_level_css(self.css)
        offsets = {"photo": 0}
        for name in BOTTOM_ROW[1:]:
            match = re.search(r"(?m)^\." + re.escape(name) + r"\s*\{[^}]*?\bleft:\s*(\d+)px", css)
            self.assertTrue(match, f"No base left offset for .{name}")
            offsets[name] = int(match.group(1))
        self.assertEqual([offsets[name] for name in BOTTOM_ROW], sorted(offsets.values()))

    def test_only_the_current_slide_takes_hits_and_reaches_assistive_tech(self):
        """Hidden slides stack over the shown one; opacity alone leaves them hit-testable and in the AX tree."""
        rule = lambda selector: re.search(r"(?m)^" + re.escape(selector) + r" \{(.*?)\n\}", self.css, re.DOTALL).group(1)
        self.assertRegex(rule(".mySlides"), r"pointer-events:\s*none")
        self.assertRegex(rule(".mySlides"), r"visibility:\s*hidden")
        self.assertRegex(rule(".mySlides.is-current"), r"pointer-events:\s*auto")
        self.assertRegex(rule(".mySlides.is-current"), r"visibility:\s*visible")
        self.assertIn('classList.add("is-current")', self.html)
        self.assertIn('classList.remove("is-current")', self.html)

    def test_slides_crossfade_without_a_keyframe_fade(self):
        """A keyframe animation would override the inline opacity that hides the previous slide."""
        self.assertNotRegex(self.css, r"@(?:-webkit-)?keyframes\s+fade\b")
        self.assertNotRegex(self.css, r"animation(?:-name)?\s*:\s*fade\b")
        reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", self.css, re.DOTALL)
        self.assertTrue(reduced, "No reduced-motion block")
        self.assertIn(".mySlides", reduced.group(1), "Reduced motion turns slides without the crossfade")

    def test_short_screens_keep_the_artist_credit(self):
        """Tiers that drop credits keep the artist's row, so no layout shows an artwork without its artist."""
        self.assertIn('"notes-artist"', self.html, "metaRow() tags the artist's row")
        self.assertNotRegex(self.css, r"\.notes-meta\s*\{[^}]*display:\s*none")
        for selector in re.findall(r"([^{}/]*\.notes-meta[^{}]*)\{[^}]*display:\s*none", self.css):
            for part in selector.split(","):
                if ".notes-meta" in part and "::before" not in part:
                    self.assertIn(":not(.notes-artist)", part)

    def test_short_landscape_arrows_stay_below_the_language_toggle(self):
        """Sideways phones: the next arrow shares its column with the language toggle, and below about 345px of
        height the stage's centre comes within the toggle's reach, so the arrows' centre is clamped below it."""
        base = top_level_css(self.css)
        block = media_block(self.css, "(orientation: landscape) and (max-height: 560px)")
        self.assertTrue(block, "No short-landscape block")
        lengths = {
            "toggle top": px(css_value(base, [".lang-toggle"], "top")),
            "toggle height": px(css_value(base, [".bt"], "height")),
            "toggle padding": px(css_value(base, [".bt"], "padding")),
            "arrow margin-top": px((css_value(base, [".prev", ".next"], "margin") or "").split(" ")[0]),
            "stage margin-top": px(css_value(block, [".slideshow-container"], "margin-top")),
        }
        for name, length in lengths.items():
            self.assertIsNotNone(length, f"No {name} in px in src/slideshow.css")
        clamp = re.fullmatch(r"max\(50%,\s*(\d+)px\)", css_value(block, [".prev", ".next"], "top") or "")
        self.assertTrue(clamp, "Short landscape screens clamp the arrows' top with max(50%, <n>px)")
        arrow_top = lengths["stage margin-top"] + int(clamp.group(1)) + lengths["arrow margin-top"]
        toggle_bottom = lengths["toggle top"] + lengths["toggle height"] + 2 * lengths["toggle padding"]
        self.assertGreaterEqual(arrow_top, toggle_bottom, "The arrows' top edge clears the bottom of the language toggle")

    def test_the_wrapped_signature_leaves_the_language_toggle_its_taps(self):
        """Wrapped on a narrow phone (about 330px wide with the shipped font), the fixed signature's box spans to the
        right edge of the screen, over the top of the toggle; only its links take pointer events."""
        base = top_level_css(self.css)
        self.assertEqual(css_value(base, [".signature"], "pointer-events"), "none")
        self.assertEqual(css_value(base, [".signature a"], "pointer-events"), "auto")

    def test_screenshot_overlay_does_not_live_in_the_url(self):
        self.assertNotIn(":target", self.css, "The overlay must not depend on the URL fragment")
        self.assertNotRegex(self.html, r"location\.(?:href|hash)\s*=")
        self.assertNotIn('href="#"', self.html)

    def test_visible_attribution_link(self):
        links = [node for node in self.nodes if node.tag == "a" and node.attrs.get("href") == NOTICE_URL]
        self.assertEqual(len(links), 1, "The slideshow shows one Attribution link to the rendered NOTICE")
        self.assertIn("signature", links[0].parent.classes, "It sits in the always-visible signature")
        key = links[0].attrs.get("data-i18n")
        self.assertTrue(key)
        self.assert_translated(key)
        self.assertEqual(links[0].text.strip(), self.messages["en"][key])


if __name__ == "__main__":
    unittest.main()
