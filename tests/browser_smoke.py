"""Run against an existing local server; requires Playwright and Chrome."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urljoin, urlparse

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base", default="http://127.0.0.1:8765")
parser.add_argument("--browser", default="/usr/bin/google-chrome")
args = parser.parse_args()
assert urlparse(args.base).hostname in {"127.0.0.1", "localhost"}, "Use a local test server"
base = args.base.rstrip("/") + "/"
mount_path = urlparse(base).path


def mounted_url(relative, source=base):
    url = urljoin(source, relative)
    assert urlparse(url).netloc == urlparse(base).netloc, url
    assert urlparse(url).path.startswith(mount_path), f"Resource escaped the site mount: {url}"
    return url

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.browser, headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, requests, bad_responses = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.on("response", lambda response: bad_responses.append((response.status, response.url)) if response.status >= 400 else None)
    page.goto(mounted_url("archive.html"), wait_until="load")
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
    if page.evaluate("window.DEEMO_CATALOG.summary.by_family.artists || 0"):
        page.locator("#family").select_option("artists")
        assert page.locator(".card").count() > 0
        page.locator("#minimum").select_option("3000")
        assert page.locator(".card").count() > 0
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
    page.locator("#reset-filters").click()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".card-image").first.click()
    assert page.locator("#close").is_visible()
    page.locator("#close").click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    asset_id = page.evaluate("window.DEEMO_CATALOG.assets.find(a => a.gallery && a.kind !== 'reference').id")
    for requested_id in dict.fromkeys((asset_id, "legacy:magnolia")):
        page.goto(mounted_url("index.html?asset=" + quote(requested_id)), wait_until="load")
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
    external = [url for url in requests if urlparse(url).scheme in {"http", "https"} and urlparse(url).hostname not in {"127.0.0.1", "localhost"}]
    assert not external, external
    outside_mount = [url for url in requests if urlparse(url).scheme in {"http", "https"} and not urlparse(url).path.startswith(mount_path)]
    assert not outside_mount, outside_mount
    assert not bad_responses, bad_responses
    assert not errors, errors
    print(json.dumps({"gallery_images": total, "base": base, "browser_errors": errors, "bad_responses": bad_responses, "external_requests": external, "checks": ["search", "empty state", "reset", "pagination", "artist/size filters", "legacy variant download/hash", "modal", "mobile layout", "artist/legacy slideshow deep links", "navigation", "manifest/browserconfig icons", "mount-relative assets", "local-only requests"]}, indent=2))
    browser.close()
