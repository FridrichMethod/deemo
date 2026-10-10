"""Browser acceptance checks for archive.html and the slideshow (index.html).

Run against an existing local server, for example `python -I -m http.server 8765 --bind 127.0.0.1` from the
repository root; Playwright drives an installed Chrome. Besides its own assertions, the run fails on any uncaught
exception, console error, HTTP error, failed request or request that leaves the local server, apart from the
failures it causes itself (see Monitor)."""

import argparse
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import quote, unquote, urljoin, urlparse

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--base", default="http://127.0.0.1:8765")
parser.add_argument("--browser", default="/usr/bin/google-chrome")
args = parser.parse_args()
assert urlparse(args.base).hostname in {"127.0.0.1", "localhost"}, "Use a local test server"
base = args.base.rstrip("/") + "/"
mount_path = urlparse(base).path
checks = []
NOTICE_URL = "https://github.com/FridrichMethod/deemo/blob/main/NOTICE"
# Withdrawn for licensing reasons: their @font-face rules name only local() fonts, so nothing may request the files.
REMOVED_FONTS = ("assets/site/fonts/COPRGTL.ttf", "assets/site/fonts/RocknRoll_Typo_bold.ttf")
HAS_HAN = "(text) => /[\\u3400-\\u9fff]/.test(text)"
IMAGE_SHOWN = "(() => { const img = document.getElementById('full-image'); return img.complete && img.naturalWidth > 0; })()"
PARAGRAPHS = "[...document.querySelectorAll('#provenance p')].map((p) => p.textContent)"
SHOWN_ID = "imgTargets[slideIndex - 1].dataset.id"
SLIDE_SHOWN = "imgTargets[slideIndex - 1].naturalWidth > 0 && imgTargets[slideIndex - 1].closest('.mySlides').classList.contains('is-current')"
# The slideshow's round controls along the bottom, left to right on screen.
BOTTOM_ROW = ["photo", "github", "archive-link", "iplayer", "random"]
# Controls by role and the message key of their accessible name.
NAMED_CONTROLS = [
    ("button", "slideshow.nav.prev"), ("button", "slideshow.nav.next"), ("button", "slideshow.screenshot.take"),
    ("button", "slideshow.music.label"), ("button", "slideshow.autoplay.label"), ("button", "lang.toggle"),
    ("link", "slideshow.github.label"), ("link", "slideshow.archive.text"), ("link", "slideshow.attribution"),
]
# Liner-notes titles ([text, data-length]) of wiki slides: a title that is only the file's key gives the headline
# to the mapped song and follows as a quiet file reference; a key that is the song's name stays the headline.
NOTES_CASES = {
    "wikis:fandom:0813480151e3f1b9": [["Tristesse", ""], ["Classic01", "file"]],
    "wikis:fandom:9eb27d23921f3320": [["For Sis", ""], ["Celia02forsis fc", "file"]],
    "wikis:fandom:082622a106c6977b": [["Code : 11", ""], ["Code11", "file"]],
    "wikis:fandom:7f71c166494b2ff8": [["Cloud9", ""]],
    "wikis:fandom:3a93b33265bbb130": [["Continuum", ""]],
}
NOTES_TITLES = "[...document.querySelectorAll('.slide-notes .notes-title')].map((title) => [title.textContent.replace(/\\u00a0/g, ' '), title.dataset.length || ''])"


def mounted_url(relative, source=base):
    url = urljoin(source, relative)
    assert urlparse(url).netloc == urlparse(base).netloc, url
    assert urlparse(url).path.startswith(mount_path), f"Resource escaped the site mount: {url}"
    return url


class Monitor:
    """Collects every sign of trouble on the pages it watches: uncaught exceptions, console errors, HTTP errors and
    failed requests. Three kinds of failure are the smoke's own doing and are tolerated:
    - loads it makes fail on purpose: expect(url) first, and the failure must then really happen;
    - requests still pending when it leaves a page (leave() first lets the page's loads finish);
    - Chrome's media loader dropping its own audio request after a successful response: with the file in its cache
      it pauses the download by cancelling it (net::ERR_ABORTED) and asks for the rest when it needs it."""

    def __init__(self):
        self.problems, self.urls = [], []
        self.expected = {}
        self.pending = {}
        self.released, self.answered = set(), set()

    def watch(self, page):
        pending = self.pending.setdefault(page, set())

        def request(request):
            self.urls.append(request.url)
            pending.add(request)

        def failed(request):
            pending.discard(request)
            aborted = request.failure == "net::ERR_ABORTED"
            if aborted and (request in self.released or (request.resource_type == "media" and request in self.answered)):
                return
            if not self.deliberate(request.url):
                self.problems.append(f"request failed ({request.failure}): {request.url}")

        def response(response):
            if response.status < 400:
                self.answered.add(response.request)
            elif not self.deliberate(response.url):
                self.problems.append(f"HTTP {response.status}: {response.url}")

        def console(message):
            if message.type == "error" and not self.deliberate(message.location.get("url", "")):
                self.problems.append(f"console error on {page.url}: {message.text}")

        page.on("request", request)
        page.on("requestfinished", lambda request: pending.discard(request))
        page.on("requestfailed", failed)
        page.on("response", response)
        page.on("console", console)
        page.on("pageerror", lambda error: self.problems.append(f"uncaught exception on {page.url}: {error}"))

    def deliberate(self, url):
        if url not in self.expected:
            return False
        self.expected[url] += 1
        return True

    def expect(self, url):
        """The smoke is about to make this URL fail: its failure signals are not problems, but one must occur."""
        self.expected.setdefault(url, 0)

    def settle(self, page, timeout=15):
        """Wait until the page has no pending loads other than media streams."""
        pending = self.pending.setdefault(page, set())
        deadline = time.monotonic() + timeout
        while any(request.resource_type != "media" for request in pending) and time.monotonic() < deadline:
            page.wait_for_timeout(50)

    def leave(self, page):
        """Let the page's loads finish, then release what is left: the smoke is about to navigate away from or close
        the page, which cuts those requests short."""
        self.settle(page)
        self.released |= self.pending[page]
        self.pending[page].clear()

    def verdict(self):
        external = [url for url in self.urls if urlparse(url).scheme in {"http", "https"} and urlparse(url).hostname not in {"127.0.0.1", "localhost"}]
        outside_mount = [url for url in self.urls if urlparse(url).scheme in {"http", "https"} and not urlparse(url).path.startswith(mount_path)]
        assert not external, external
        assert not outside_mount, outside_mount
        assert not self.problems, "\n".join(self.problems)
        unused = [url for url, seen in self.expected.items() if not seen]
        assert not unused, f"Loads the smoke made fail never failed: {unused}"
        return external


monitor = Monitor()


def new_page(browser, width=1440, height=1000, **options):
    """A page in a context of its own (fresh storage and cache), watched by the monitor."""
    page = browser.new_context(viewport={"width": width, "height": height}, **options).new_page()
    monitor.watch(page)
    return page


def navigate(page, relative):
    monitor.leave(page)
    page.goto(mounted_url(relative), wait_until="load")


def finish(page):
    monitor.leave(page)
    page.context.close()


def shown_count(page):
    """The number of matching images the archive reports ("{count} / {total} …" in either language)."""
    return page.evaluate("parseInt(document.getElementById('count').textContent, 10)")


def catalog_count(page, predicate):
    """The number of gallery images in the page's catalog for which the JavaScript predicate holds."""
    return page.evaluate(f"window.DEEMO_CATALOG.assets.filter((a) => a.gallery && ({predicate})(a)).length")


def card_sizes(page):
    return page.evaluate("[...document.querySelectorAll('.card-image img')].map((img) => [+img.getAttribute('width'), +img.getAttribute('height')])")


def check_archive(page):
    navigate(page, "archive.html")
    page.wait_for_selector(".card-image img")
    total = page.evaluate("window.DEEMO_CATALOG.summary.gallery_images")
    assert total >= 320
    assert total == catalog_count(page, "() => true") == shown_count(page)
    page.wait_for_function("document.querySelector('.card-image img').naturalWidth > 0")
    page.locator("#query").fill("Magnolia")
    assert 1 <= page.locator(".card").count() == shown_count(page) < total
    page.locator(".card-image").first.click()
    assert page.locator("#viewer").evaluate("el => el.open")
    page.wait_for_function("document.getElementById('full-image').naturalWidth > 0")
    assert page.locator("#download").get_attribute("href")
    page.keyboard.press("Escape")
    page.locator("#query").fill("definitely-no-such-deemo-song-xyz")
    assert page.locator("#empty").is_visible()
    page.locator("#reset-filters").click()
    assert page.locator(".card").count() == min(60, total)
    if total > 60:
        page.locator("#more").click()
        assert page.locator(".card").count() == min(120, total)
    checks.extend(["search", "empty state", "reset", "pagination"])
    # Each filter and sort is checked against the catalog: the count it reports and the cards it draws.
    artists = "(a) => a.provenance.some((p) => p.family === 'artists')"
    artist_count = catalog_count(page, artists)
    assert artist_count, "The catalog has artist images"
    page.locator("#family").select_option("artists")
    assert shown_count(page) == artist_count
    page.locator("#minimum").select_option("3000")
    large = catalog_count(page, f"(a) => ({artists})(a) && Math.max(a.width, a.height) >= 3000")
    assert 0 < large < artist_count, "The 3000 px filter keeps some artist images and drops others"
    assert shown_count(page) == large
    sizes = card_sizes(page)
    assert len(sizes) == min(60, large) and all(max(size) >= 3000 for size in sizes), sizes
    page.locator("#reset-filters").click()
    page.locator("#kind").select_option("collection_cover")
    covers = catalog_count(page, "(a) => a.provenance.some((p) => p.kind === 'collection_cover')")
    assert 0 < covers < total and shown_count(page) == covers
    page.locator("#reset-filters").click()
    page.locator("#sort").select_option("resolution")
    areas = [width * height for width, height in card_sizes(page)]
    assert areas == sorted(areas, reverse=True) and len(areas) == min(60, total)
    assert areas[0] == page.evaluate("Math.max(...window.DEEMO_CATALOG.assets.filter((a) => a.gallery).map((a) => a.width * a.height))")
    checks.extend(["source/kind/size filters match the catalog", "pixel-area sort"])
    page.locator("#reset-filters").click()
    page.locator("#family").select_option("legacy")
    page.locator("#query").fill("Magnolia")
    assert page.locator(".card").count() >= 1
    page.locator(".card-image").first.click()
    page.wait_for_function("document.getElementById('full-image').naturalWidth > 0")
    variant_link = page.locator(".legacy-variant-link").first
    assert variant_link.is_visible()
    variant_href = variant_link.get_attribute("href")
    assert "assets/legacy/tiny/" in unquote(variant_href)
    # The copy shares its basename with the original, so its role goes into the saved name.
    assert variant_link.get_attribute("download") == "magnolia-palette-quantized.png"
    assert page.locator("#download").get_attribute("download") == "magnolia.png"
    variant = page.evaluate("window.DEEMO_CATALOG.assets.flatMap(a => a.provenance.flatMap(p => p.variants || [])).find(v => v.id === 'legacy:tiny:magnolia')")
    assert variant
    assert urlparse(mounted_url(variant_href)).path == urlparse(mounted_url(variant["url"])).path
    response = page.request.get(mounted_url(variant_href))
    assert response.status == 200
    assert len(response.body()) == variant["bytes"]
    assert hashlib.sha256(response.body()).hexdigest() == variant["sha256"]
    page.keyboard.press("Escape")
    checks.extend(["legacy variant download/hash/name", "modal"])
    page.locator("#reset-filters").click()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".card-image").first.click()
    assert page.locator("#close").is_visible()
    page.locator("#close").click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    checks.append("mobile layout")
    return total


def check_slideshow_deep_links(page):
    asset_id = page.evaluate("window.DEEMO_CATALOG.assets.find(a => a.gallery && a.kind !== 'reference').id")
    for requested_id in dict.fromkeys((asset_id, "legacy:magnolia")):
        navigate(page, "index.html?asset=" + quote(requested_id))
        page.wait_for_function("document.querySelector('.deemo-view').style.opacity === '1'")
        page.wait_for_function("imgTargets[slideIndex - 1].naturalWidth > 0")
        assert page.evaluate("imgTargets[slideIndex - 1].dataset.id") == requested_id
        assert page.evaluate("randomFlag") == 1
        if requested_id.startswith("legacy:"):
            assert "assets/legacy/trans/" in page.evaluate("imgTargets[slideIndex - 1].src")
        original_title = page.locator("#asset-title").inner_text()
        page.locator(".next").click()
        assert page.evaluate("imgTargets[slideIndex - 1].dataset.id") != requested_id, "Next shows another artwork"
        assert page.locator("#asset-title").inner_text() == page.evaluate("imgTargets[slideIndex - 1].dataset.title")
        page.wait_for_function("imgTargets[slideIndex - 1].naturalWidth > 0")
        page.locator(".prev").click()
        assert page.evaluate("imgTargets[slideIndex - 1].dataset.id") == requested_id, "Previous returns to it"
        assert page.locator("#asset-title").inner_text() == original_title
    checks.extend(["artist/legacy slideshow deep links", "navigation"])


def check_language(browser):
    """A fresh context is English by default; the toggle switches to Simplified Chinese, keeps filters and persists."""
    page = new_page(browser)
    heading_has_han = "/[\\u3400-\\u9fff]/.test(document.querySelector('h1').textContent)"
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    assert page.evaluate("document.documentElement.lang") == "en"
    assert not page.evaluate(heading_has_han)
    page.locator("#family").select_option("legacy")
    english_count = page.locator("#count").inner_text()
    page.locator("[data-lang-toggle]").first.click()
    assert page.evaluate("document.documentElement.lang") == "zh-CN"
    assert page.evaluate(heading_has_han)
    assert "lang=zh-CN" in page.url and "family=legacy" in page.url
    assert page.locator("#family").input_value() == "legacy"
    assert page.locator("#count").inner_text() != english_count
    page.locator(".card-image").first.click()
    assert "原仓库 · 游戏纹理" in page.locator("#provenance").inner_text()
    page.keyboard.press("Escape")
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    assert page.evaluate("document.documentElement.lang") == "zh-CN", "language choice should persist"
    navigate(page, "index.html?asset=" + quote("legacy:magnolia"))
    page.wait_for_function("document.querySelector('.deemo-view').style.opacity === '1'")
    assert page.locator("#asset-source").inner_text() == "原仓库 · 游戏纹理"
    assert "lang=zh-CN" in page.locator(".archive-link a").get_attribute("href")
    page.locator("[data-lang-toggle]").first.click()
    assert page.evaluate("document.documentElement.lang") == "en"
    assert page.locator("#asset-source").inner_text() == "Original repository · game textures"
    assert "lang=" not in page.url and "lang=" not in page.locator(".archive-link a").get_attribute("href")
    finish(page)
    checks.append("language toggle, persistence and localized source names")


def check_language_before_catalog(browser):
    """Translations apply once the DOM is parsed, not after the multi-megabyte catalog script (held back here)."""
    page = new_page(browser)
    held = []
    page.route("**/data/catalog.js", lambda route: held.append(route))
    page.goto(mounted_url("archive.html?lang=zh-CN"), wait_until="commit")
    page.wait_for_function(f"({HAS_HAN})(document.querySelector('h1')?.textContent || '')", timeout=5000)
    assert page.evaluate("window.DEEMO_CATALOG === undefined"), "data/catalog.js is still held back"
    assert page.locator("[data-lang-toggle]").inner_text() == "English"
    page.locator("[data-lang-toggle]").click()
    assert page.evaluate("[DEEMO_I18N.lang, document.documentElement.lang]") == ["en", "en"], "The toggle goes where its label points"
    for route in held:
        route.continue_()
    page.wait_for_selector(".card")
    assert page.evaluate(f"!({HAS_HAN})(document.querySelector('h1').textContent)")
    finish(page)
    # Until src/i18n/common.js registers, the toggle keeps the English page's static label, which names zh-CN.
    page = new_page(browser)
    held = []
    page.route("**/src/i18n/common.js", lambda route: held.append(route))
    page.goto(mounted_url("archive.html?lang=zh-CN"), wait_until="commit")
    page.wait_for_function("window.DEEMO_I18N && document.readyState !== 'loading'", timeout=5000)
    assert page.locator("[data-lang-toggle]").inner_text() == "中文"
    page.locator("[data-lang-toggle]").click()
    assert page.evaluate("DEEMO_I18N.lang") == "zh-CN", "A click on the static 中文 label keeps Chinese"
    for route in held:
        route.continue_()
    page.wait_for_selector(".card")
    assert page.locator("[data-lang-toggle]").inner_text() == "English"
    finish(page)
    checks.append("translations before the catalog loads; toggle follows its label")


def check_explicit_language_persists(browser):
    """An explicit ?lang=en replaces a stored zh-CN, so the reload (which drops ?lang= for English) and the
    slideshow stay English."""
    page = new_page(browser)
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    page.locator("[data-lang-toggle]").click()
    assert page.evaluate("localStorage.getItem('deemo-lang')") == "zh-CN"
    navigate(page, "archive.html?lang=en")
    page.wait_for_selector(".card")
    assert page.evaluate("document.documentElement.lang") == "en"
    monitor.leave(page)
    page.reload(wait_until="load")
    page.wait_for_selector(".card")
    assert "lang=" not in page.url and page.evaluate("document.documentElement.lang") == "en"
    monitor.leave(page)
    with page.expect_navigation(wait_until="load"):
        page.locator("nav a[data-lang-link]").click()
    assert urlparse(page.url).path.endswith("/index.html")
    assert page.evaluate("document.documentElement.lang") == "en"
    finish(page)
    checks.append("?lang=en persists over a stored zh-CN")


def check_search_folding(browser):
    """Full-width, half-width katakana and full-width colon queries find what their plain forms find, and case
    folding ignores the browser locale (Turkish lowercases I to a dotless i)."""
    counts = {}
    folded = [("magnolia", "Ｍａｇｎｏｌｉａ"), ("まとめ", "マトメ"), ("まとめ", "ﾏﾄﾒ"), ("Re: the Full moon", "Re：the Full moon"), ("AD:PIANO", "AD：PIANO")]
    cased = ["ice collection", "ICE COLLECTION", "In a cradle"]
    for locale in ("en-US", "tr-TR"):
        page = new_page(browser, locale=locale)
        navigate(page, "archive.html")
        page.wait_for_selector(".card")
        if locale == "tr-TR":
            # (V8 lowercases a short ASCII string without the locale; the kana sends this one through it.)
            assert page.evaluate("'Ice ころ'.toLocaleLowerCase()") == "ıce ころ", "The Turkish context lowercases I to a dotless i"
        for query in [query for pair in folded for query in pair] + cased:
            page.locator("#query").fill(query)
            counts[locale, query] = shown_count(page)
        finish(page)
    for plain, variant in folded:
        assert counts["en-US", plain] == counts["en-US", variant] > 0, (plain, variant, counts)
    for query in cased:
        assert counts["tr-TR", query] == counts["en-US", query] > 0, (query, counts)
    checks.append("search folds full-width, kana and colon forms in any locale")


def check_viewer_scroll_and_focus(browser):
    """The page stays put under the open viewer, and closing it focuses the card of the image last shown."""
    page = new_page(browser, 1440, 900)
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    focus = """() => { const cards = [...document.querySelectorAll('.card-image')], box = document.activeElement.getBoundingClientRect();
        return [cards.indexOf(document.activeElement), box.bottom > 0 && box.top < innerHeight]; }"""

    def close_viewer():
        # The dialog's close event, which moves the focus, follows the key press as a separate task.
        page.keyboard.press("Escape")
        page.wait_for_function("!document.getElementById('viewer').open")
        page.wait_for_timeout(100)
        return page.evaluate(focus)

    page.locator(".card-image").nth(5).click()
    page.wait_for_function(IMAGE_SHOWN)
    before = page.evaluate("scrollY")
    box = page.locator(".viewer-image").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for _ in range(3):
        page.mouse.wheel(0, 800)
    page.mouse.move(4, 450)
    for _ in range(3):
        page.mouse.wheel(0, 800)
    page.locator("#close").focus()
    page.keyboard.press("PageDown")
    page.keyboard.press("End")
    page.wait_for_timeout(400)
    assert page.evaluate("scrollY") == before, "The page behind the viewer scrolled"
    assert close_viewer() == [5, True]
    # Past the 60 cards drawn so far: closing draws up to the image shown and focuses its card.
    page.locator(".card-image").nth(59).click()
    page.wait_for_function(IMAGE_SHOWN)
    for _ in range(2):
        page.locator("#next").click()
        page.wait_for_function(IMAGE_SHOWN)
    assert page.locator("#position").inner_text().startswith("62 / ")
    assert close_viewer() == [61, True]
    finish(page)
    checks.append("viewer scroll lock and focus return")


def check_deep_link_filters(browser):
    page = new_page(browser)
    # Values that match no option are ignored, so each select keeps showing the default it applies.
    navigate(page, "archive.html?minimum=4000&sort=bogus&family=nope&kind=x")
    page.wait_for_selector(".card")
    state = page.evaluate("Object.fromEntries(['minimum', 'sort', 'family', 'kind'].map((id) => [id, [document.getElementById(id).value, document.getElementById(id).selectedIndex]]))")
    assert state == {"minimum": ["0", 0], "sort": ["source", 0], "family": ["", 0], "kind": ["", 0]}, state
    assert shown_count(page) == catalog_count(page, "() => true")
    # Title sort compares numbers as numbers: page 2 before page 10.
    prefix = "AD:PIANO Collection 2 — page "
    navigate(page, "archive.html?sort=title&query=" + quote(prefix.split(" — ")[0]))
    page.wait_for_selector(".card")
    pages = [int(title[len(prefix):]) for title in page.locator(".card h2").all_inner_texts() if title.startswith(prefix)]
    assert len(pages) == catalog_count(page, f"(a) => a.title.startsWith({json.dumps(prefix)})") >= 10, pages
    assert pages == sorted(pages), pages
    finish(page)
    checks.extend(["invalid deep-link filters ignored", "numeric title sort"])


def viewer_record(page):
    """The asset the viewer shows (by its file path) and what its provenance block should say in the page language."""
    return page.evaluate("""() => {
        const {t} = window.DEEMO_I18N, path = document.getElementById('file-info').textContent.split('\\n')[0];
        const asset = window.DEEMO_CATALOG.assets.find((a) => a.path === path), expected = [];
        for (const p of asset.provenance) {
            if (p.composer) expected.push(['composer', t('provenance.composer', {composer: p.composer})]);
            // Every wiki upload carries the same lineage caveat, labelled as the "wiki" class.
            const token = p.family === 'wikis' ? 'wiki' : p.provenance;
            if (typeof p.provenance === 'string' && p.provenance) expected.push([`class ${token}`, t('provenance.class', {label: t(`provenance.class.${token}`)})]);
        }
        return {id: asset.id, expected, shown: [...document.querySelectorAll('#provenance p')].map((p) => p.textContent)};
    }""")


def check_viewer_provenance(browser):
    """The viewer states each record's composer and provenance class in the page language; a record a refetch kept
    with an upstream_status gets a localized line for it."""
    page = new_page(browser)
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    # "Altale" has a legacy texture (with its composer), wiki uploads and a community repost.
    page.locator("#query").fill("Altale")
    page.locator(".card-image").first.click()
    seen = set()
    for _ in range(shown_count(page)):
        page.wait_for_function(IMAGE_SHOWN)
        for lang in ("en", "zh-CN"):
            # The modal viewer covers the toggle; a language change redraws the open viewer.
            page.evaluate("(lang) => DEEMO_I18N.setLang(lang)", lang)
            record = viewer_record(page)
            for label, text in record["expected"]:
                assert text in record["shown"], (lang, record)
                assert lang == "en" or page.evaluate(HAS_HAN, text), (lang, text)
                seen.add(label.split(" ")[0] if label == "composer" else "wiki class" if label == "class wiki" else "other class")
        page.locator("#next").click()
    assert seen == {"composer", "wiki class", "other class"}, seen
    page.keyboard.press("Escape")
    # No committed record has an upstream_status yet, so give the first gallery image one and compare its block.
    page.locator("#reset-filters").click()
    page.locator(".card-image").first.click()
    page.wait_for_function(IMAGE_SHOWN)
    target = "window.DEEMO_CATALOG.assets.find((a) => a.gallery).provenance[0]"
    blocks = {}
    for status in (None, "removed"):
        page.evaluate(f"(status) => {{ if (status) {target}.upstream_status = status; else delete {target}.upstream_status; }}", status)
        for lang in ("zh-CN", "en"):
            page.evaluate("(lang) => DEEMO_I18N.setLang(lang)", lang)
            blocks[status, lang] = page.evaluate(PARAGRAPHS)
    for lang in ("en", "zh-CN"):
        added = list((Counter(blocks["removed", lang]) - Counter(blocks[None, lang])).elements())
        assert added and not Counter(blocks[None, lang]) - Counter(blocks["removed", lang]), (lang, blocks)
        for text in added:
            assert "removed" != text.strip() and "upstream_status" not in text, text
            assert lang == "en" or page.evaluate(HAS_HAN, text), text
    page.keyboard.press("Escape")
    finish(page)
    checks.append("viewer composer, provenance class and upstream status, in both languages")


def check_previews(browser):
    """Grid cards show the derived previews and request no original; the viewer, its download link and its link
    to the original serve the original file; a card whose preview fails falls back to the original."""
    page = new_page(browser, 1440, 900)
    requested = []
    page.on("request", lambda request: requested.append(urlparse(request.url).path))
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    monitor.settle(page)
    cards = page.evaluate("""() => { const assets = window.DEEMO_CATALOG.assets.filter((a) => a.gallery);
        return [...document.querySelectorAll('.card-image img')].map((img, index) => [img.getAttribute('src'), assets[index].thumb || null, assets[index].url]); }""")
    assert len(cards) == 60 and all(thumb for _, thumb, _ in cards), "The first cards all have previews"
    assert all(src == thumb for src, thumb, _ in cards), cards[:3]
    originals = {urlparse(mounted_url(url)).path for _, _, url in cards}
    assert not originals & set(requested), sorted(originals & set(requested))[:3]
    image_bytes = page.evaluate("performance.getEntriesByType('resource').filter((e) => e.initiatorType === 'img').reduce((sum, e) => sum + e.encodedBodySize, 0)")
    assert 0 < image_bytes < 2_000_000, f"The first screen of the grid loaded {image_bytes} bytes of images"
    page.locator(".card-image").first.click()
    page.wait_for_function(IMAGE_SHOWN)
    url = cards[0][2]
    assert urlparse(page.evaluate("document.getElementById('full-image').currentSrc")).path == urlparse(mounted_url(url)).path
    assert page.locator("#download").get_attribute("href") == url == page.locator("#original").get_attribute("href")
    assert urlparse(mounted_url(url)).path in requested
    finish(page)
    # The first card's preview fails on purpose.
    page = new_page(browser, 1440, 900)
    preview = mounted_url(cards[0][1])
    monitor.expect(preview)
    page.route(preview, lambda route: route.abort())
    navigate(page, "archive.html")
    page.wait_for_function("(() => { const img = document.querySelector('.card-image img'); return img.complete && img.naturalWidth > 0; })()", timeout=10_000)
    assert page.locator(".card-image img").first.get_attribute("src") == cards[0][2]
    finish(page)
    checks.append(f"grid previews ({image_bytes} bytes of images on the first screen), originals in the viewer, fallback")


def check_attribution(browser):
    """Both pages link "Attribution" to the rendered NOTICE, in both languages."""
    for lang in ("en", "zh-CN"):
        page = new_page(browser)
        for relative, link in (("archive.html", "footer a[data-i18n='footer.attribution']"), ("index.html?asset=legacy%3Amagnolia", ".signature a[data-i18n='slideshow.attribution']")):
            navigate(page, f"{relative}{'&' if '?' in relative else '?'}lang={lang}")
            assert page.locator(link).count() == 1, (relative, link)
            label = page.evaluate("(key) => DEEMO_I18N.t(key)", page.locator(link).get_attribute("data-i18n"))
            assert page.locator(link).get_attribute("href") == NOTICE_URL, relative
            assert page.locator(link).is_visible() and page.locator(link).inner_text() == label, (relative, label)
            assert (lang == "zh-CN") == page.evaluate(HAS_HAN, label), label
            # Every font the page uses has been looked up, so a font file it still referenced would have been requested.
            page.evaluate("document.fonts.ready.then(() => document.fonts.size)")
        finish(page)
    checks.append("Attribution links to NOTICE on both pages")


def check_removed_files(page):
    """No request for the withdrawn fonts, which are not served either, and no BWIKI title-tab sprite in the data."""
    fonts = [url for url in monitor.urls if urlparse(url).path.endswith(REMOVED_FONTS)]
    assert not fonts, fonts
    for path in REMOVED_FONTS:
        assert page.request.get(mounted_url(path)).status == 404, path
    navigate(page, "index.html")
    page.wait_for_function("window.DEEMO_I18N && imgTargets.length > 0")
    slides = page.evaluate("[...imgTargets].filter((img) => /titletab/i.test(`${img.dataset.src} ${img.dataset.title} ${img.dataset.id}`)).map((img) => img.dataset.id)")
    assert not slides, slides
    navigate(page, "archive.html")
    page.wait_for_selector(".card")
    sprites = page.evaluate("window.DEEMO_CATALOG.assets.filter((a) => /titletab/i.test(JSON.stringify(a))).map((a) => a.id)")
    assert not sprites, sprites
    # They were 70 x 47 UI tabs imported as collection covers.
    icons = catalog_count(page, "(a) => a.provenance.some((p) => p.kind === 'collection_cover') && Math.max(a.width, a.height) < 100")
    assert icons == 0, icons
    checks.append("no removed fonts requested or served; no title-tab sprites")


def open_slideshow(browser, query, width=1440, height=1000):
    page = new_page(browser, width, height)
    navigate(page, "index.html?" + query)
    page.wait_for_function("document.querySelector('.deemo-view').style.opacity === '1'")
    page.wait_for_function(SLIDE_SHOWN)
    return page


def hit(page, selector):
    """Whether a tap at the centre of the first element matching the selector reaches it."""
    return page.locator(selector).first.evaluate("""(node) => { const box = node.getBoundingClientRect();
        return node.contains(document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2)); }""")


def check_slideshow_controls(browser):
    """Each control carries its translated accessible name in both languages. The arrow keys turn the slides, but
    not while a form field has focus; Tab runs along the bottom row as it is drawn; the sand clock reports whether
    autoplay runs."""
    for lang in ("en", "zh-CN"):
        page = open_slideshow(browser, f"asset=legacy%3Amagnolia&lang={lang}")
        for role, key in NAMED_CONTROLS:
            name = page.evaluate("(key) => DEEMO_I18N.t(key)", key)
            assert page.get_by_role(role, name=name, exact=True).count() == 1, (lang, role, name)
            assert key == "lang.toggle" or (lang == "zh-CN") == page.evaluate(HAS_HAN, name), (lang, name)
        # The overlay's buttons are hidden while it is closed, so read their labels.
        labels = page.locator("#screenshot button").evaluate_all("(buttons) => buttons.map((button) => button.getAttribute('aria-label'))")
        expected = page.evaluate("[DEEMO_I18N.t('slideshow.screenshot.save'), DEEMO_I18N.t('slideshow.screenshot.close')]")
        assert labels == expected and all((lang == "zh-CN") == page.evaluate(HAS_HAN, label) for label in labels), (lang, labels)
        finish(page)
    page = open_slideshow(browser, "asset=legacy%3Amagnolia")
    page.evaluate("document.activeElement.blur()")
    start = page.evaluate("slideIndex")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("slideIndex") == start + 1 and page.evaluate(SHOWN_ID) != "legacy:magnolia"
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("slideIndex") == start and page.evaluate(SHOWN_ID) == "legacy:magnolia"
    page.evaluate("document.body.append(Object.assign(document.createElement('input'), {id: 'smoke-field'}))")
    page.locator("#smoke-field").focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("slideIndex") == start, "The arrow keys belong to a focused form field"
    page.evaluate("document.getElementById('smoke-field').remove()")
    row = []
    for _ in range(40):
        page.keyboard.press("Tab")
        stop = page.evaluate("""(names) => { const control = document.activeElement.closest(names.map((name) => '.' + name).join(', '));
            return control && [control.classList[0], document.activeElement.getBoundingClientRect().left]; }""", BOTTOM_ROW)
        if stop and stop[0] not in [name for name, _ in row]:
            row.append(stop)
        if len(row) == len(BOTTOM_ROW):
            break
    assert [name for name, _ in row] == BOTTOM_ROW, row
    assert [left for _, left in row] == sorted(left for _, left in row), row
    clock = page.locator(".random button")
    assert clock.get_attribute("aria-pressed") == "false", "A deep link pauses autoplay"
    clock.click()
    assert [clock.get_attribute("aria-pressed"), page.evaluate("randomFlag")] == ["true", 0]
    clock.click()
    assert [clock.get_attribute("aria-pressed"), page.evaluate("randomFlag")] == ["false", 1]
    finish(page)
    checks.extend(["slideshow control names in both languages", "arrow keys", "bottom-row focus order", "sand clock state"])


def check_screenshot_overlay(browser):
    """A shared or reloaded #screenshot URL opens nothing. The camera opens the overlay with the focus inside and the
    arrow keys idle; Escape closes and empties it and hands the focus back, with no history entry or URL change."""
    page = open_slideshow(browser, "asset=legacy%3Amagnolia#screenshot")
    overlay_open = "document.getElementById('screenshot').classList.contains('is-open')"
    assert not page.evaluate(overlay_open)
    assert page.evaluate("getComputedStyle(document.getElementById('screenshot')).visibility") == "hidden"
    assert hit(page, ".next"), "Nothing covers the page"
    page.evaluate("downloadCanvas()")  # Save with no screenshot does nothing
    history = page.evaluate("[history.length, location.href]")
    page.locator(".photo button").click()
    page.wait_for_function(overlay_open, timeout=20_000)
    assert page.evaluate("Boolean(document.activeElement.closest('#screenshot'))")
    start = page.evaluate("slideIndex")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("slideIndex") == start, "The arrow keys wait while the overlay is open"
    page.keyboard.press("Escape")
    assert not page.evaluate(overlay_open)
    assert page.evaluate("document.getElementById('scerrn-content').childElementCount") == 0
    assert page.evaluate("document.activeElement === document.querySelector('.photo button')")
    assert page.evaluate("[history.length, location.href]") == history
    finish(page)
    checks.append("screenshot overlay: no URL state, Escape, focus return")


def check_slide_hit_testing(browser):
    """Hidden slides stack over the shown one; only the shown slide may take a tap on the artwork."""
    page = open_slideshow(browser, "asset=legacy%3Amagnolia")
    current = "imgTargets[slideIndex - 1]"
    assert page.locator(".mySlides.is-current img").evaluate(f"(img) => img === {current}")
    assert hit(page, ".mySlides.is-current img")
    for control in (".next", ".next", ".prev"):
        page.locator(control).click()
        page.wait_for_function(SLIDE_SHOWN)
        assert hit(page, ".mySlides.is-current img"), control
    finish(page)
    checks.append("only the shown slide takes taps")


def check_small_screens(browser):
    # A short landscape phone: the next arrow shares its column with the language toggle and takes every tap.
    page = open_slideshow(browser, "asset=artists%3Apixiv%3A47910628%3Ap0", 568, 320)
    assert page.locator(".lang-toggle").is_visible()
    misses = page.locator(".next").evaluate("""(arrow) => { const box = arrow.getBoundingClientRect(), radius = box.width / 2 - 1, misses = [];
        for (let dx = -radius; dx <= radius; dx += 4) for (let dy = -radius; dy <= radius; dy += 4) {
            const point = document.elementFromPoint((box.left + box.right) / 2 + dx, (box.top + box.bottom) / 2 + dy);
            if (dx * dx + dy * dy <= radius * radius && !arrow.contains(point)) misses.push(point && point.className);
        }
        return misses; }""")
    assert not misses, misses
    start = page.evaluate("slideIndex")
    page.locator(".next").click(timeout=5000)
    assert page.evaluate("slideIndex") == start + 1
    finish(page)
    # A landscape phone keeps the artist credit on screen, above the control row.
    page = open_slideshow(browser, "asset=artists%3Ajimdo%3Akolokolsan%3Aglaciology", 844, 390)
    page.wait_for_function("document.querySelector('.slide-notes dd.notes-artist')?.checkVisibility({opacityProperty: true})", timeout=5000)
    credit = page.evaluate("""(names) => { const dd = document.querySelector('.slide-notes dd.notes-artist'), box = dd.getBoundingClientRect();
        const row = Math.min(...names.map((name) => document.querySelector('.' + name).getBoundingClientRect().top));
        return {text: dd.textContent, artist: imgTargets[slideIndex - 1].dataset.artist.replace(/\\n/g, ''), top: box.top, bottom: box.bottom, row}; }""", BOTTOM_ROW)
    assert credit["text"] == credit["artist"] and 0 <= credit["top"] and credit["bottom"] <= credit["row"], credit
    finish(page)
    # A short portrait phone keeps the artwork at least as tall as the notes and caption below it.
    page = open_slideshow(browser, "asset=artists%3Apixiv%3A47910628%3Ap0", 375, 553)
    sizes = page.evaluate("""() => { const art = imgTargets[slideIndex - 1].getBoundingClientRect(), notes = document.querySelector('.liner-text').getBoundingClientRect();
        return {art: art.height, text: document.querySelector('.asset-caption').getBoundingClientRect().bottom - notes.top}; }""")
    assert sizes["art"] >= sizes["text"], sizes
    finish(page)
    checks.extend(["next arrow clear of the toggle at 568x320", "artist credit at 844x390", "artwork dominant at 375x553"])


def check_liner_notes(browser):
    page = new_page(browser)
    for asset_id, titles in NOTES_CASES.items():
        navigate(page, "index.html?asset=" + quote(asset_id))
        page.wait_for_function(f"{SHOWN_ID} === {json.dumps(asset_id)} && document.querySelector('.slide-notes .notes-title') !== null")
        assert page.evaluate(NOTES_TITLES) == titles, (asset_id, page.evaluate(NOTES_TITLES))
    # Across the whole show, no wiki slide with mapped songs gets a file-styled headline.
    styled = page.evaluate("""() => [...imgTargets].filter((img) => img.dataset.songs && /^wikis:/.test(img.dataset.sourceId || ''))
        .filter((img) => notesFor(img).querySelector('.notes-title').dataset.length === 'file').map((img) => img.dataset.id)""")
    assert not styled, styled
    finish(page)
    checks.append("liner notes headline the mapped song for a wiki file key")


def check_site_icons(page):
    manifest_url = mounted_url("site.webmanifest")
    manifest_response = page.request.get(manifest_url)
    assert manifest_response.status == 200
    for icon in manifest_response.json()["icons"]:
        icon_response = page.request.get(mounted_url(icon["src"], manifest_url))
        assert icon_response.status == 200
        assert icon_response.body()
    browserconfig_url = mounted_url("browserconfig.xml")
    browserconfig_response = page.request.get(browserconfig_url)
    assert browserconfig_response.status == 200
    for element in ET.fromstring(browserconfig_response.body()).iter():
        if "src" in element.attrib:
            icon_response = page.request.get(mounted_url(element.attrib["src"], browserconfig_url))
            assert icon_response.status == 200
            assert icon_response.body()
    checks.append("manifest/browserconfig icons")


started = time.monotonic()
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.browser, headless=True, args=["--no-sandbox"])
    page = new_page(browser)
    total = check_archive(page)
    check_slideshow_deep_links(page)
    check_language(browser)
    check_language_before_catalog(browser)
    check_explicit_language_persists(browser)
    check_search_folding(browser)
    check_viewer_scroll_and_focus(browser)
    check_deep_link_filters(browser)
    check_viewer_provenance(browser)
    check_previews(browser)
    check_attribution(browser)
    check_slideshow_controls(browser)
    check_screenshot_overlay(browser)
    check_slide_hit_testing(browser)
    check_small_screens(browser)
    check_liner_notes(browser)
    check_site_icons(page)
    check_removed_files(page)
    finish(page)
    external = monitor.verdict()
    browser.close()
checks.extend(["mount-relative assets", "local-only requests", "no console errors or failed requests"])
print(json.dumps({"gallery_images": total, "base": base, "problems": monitor.problems, "external_requests": external,
                  "seconds": round(time.monotonic() - started, 1), "checks": checks}, indent=2))
