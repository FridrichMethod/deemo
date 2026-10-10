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

    def leave(self, page, timeout=15):
        """Let the page's pending loads (other than media streams) finish, then release what is left: the smoke is
        about to navigate away from or close the page, which cuts those requests short."""
        pending = self.pending.setdefault(page, set())
        deadline = time.monotonic() + timeout
        while any(request.resource_type != "media" for request in pending) and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        self.released |= pending
        pending.clear()

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


def check_archive(page):
    navigate(page, "archive.html")
    page.wait_for_selector(".card-image img")
    total = page.evaluate("window.DEEMO_CATALOG.summary.gallery_images")
    assert total >= 320
    page.wait_for_function("document.querySelector('.card-image img').naturalWidth > 0")
    page.locator("#query").fill("Magnolia")
    assert page.locator(".card").count() >= 1
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
    if page.evaluate("window.DEEMO_CATALOG.summary.by_family.artists || 0"):
        page.locator("#family").select_option("artists")
        assert page.locator(".card").count() > 0
        page.locator("#minimum").select_option("3000")
        assert page.locator(".card").count() > 0
        checks.append("artist/size filters")
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
    assert variant_link.get_attribute("download") is not None
    variant = page.evaluate("window.DEEMO_CATALOG.assets.flatMap(a => a.provenance.flatMap(p => p.variants || [])).find(v => v.id === 'legacy:tiny:magnolia')")
    assert variant
    assert urlparse(mounted_url(variant_href)).path == urlparse(mounted_url(variant["url"])).path
    response = page.request.get(mounted_url(variant_href))
    assert response.status == 200
    assert len(response.body()) == variant["bytes"]
    assert hashlib.sha256(response.body()).hexdigest() == variant["sha256"]
    page.keyboard.press("Escape")
    checks.extend(["legacy variant download/hash", "modal"])
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
        page.locator(".prev").click()
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
    check_site_icons(page)
    finish(page)
    external = monitor.verdict()
    browser.close()
checks.extend(["mount-relative assets", "local-only requests", "no console errors or failed requests"])
print(json.dumps({"gallery_images": total, "base": base, "problems": monitor.problems, "external_requests": external,
                  "seconds": round(time.monotonic() - started, 1), "checks": checks}, indent=2))
