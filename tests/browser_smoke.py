"""Run against an existing local server; requires Playwright and Chrome."""

import argparse
import json
from urllib.parse import quote, urlparse

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base", default="http://127.0.0.1:8765")
parser.add_argument("--browser", default="/usr/bin/google-chrome")
args = parser.parse_args()
assert urlparse(args.base).hostname in {"127.0.0.1", "localhost"}, "Use a local test server"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.browser, headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, requests, bad_responses = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.on("response", lambda response: bad_responses.append((response.status, response.url)) if response.status >= 400 else None)
    page.goto(args.base + "/archive.html", wait_until="load")
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
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".card-image").first.click()
    assert page.locator("#close").is_visible()
    page.locator("#close").click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    asset_id = page.evaluate("window.DEEMO_CATALOG.assets.find(a => a.gallery && a.kind !== 'reference').id")
    page.goto(args.base + "/index.html?asset=" + quote(asset_id), wait_until="load")
    page.wait_for_function("document.querySelector('.deemo-view').style.opacity === '1'")
    page.wait_for_function("Array.from(document.querySelectorAll('.deemo-draw')).some(i => i.naturalWidth > 0)")
    assert page.evaluate("imgTargets[slideIndex - 1].dataset.id") == asset_id
    assert page.evaluate("randomFlag") == 1
    original_title = page.locator("#asset-title").inner_text()
    page.locator(".next").click()
    page.locator(".prev").click()
    assert page.locator("#asset-title").inner_text() == original_title
    external = [url for url in requests if urlparse(url).scheme in {"http", "https"} and urlparse(url).hostname not in {"127.0.0.1", "localhost"}]
    assert not external, external
    assert not bad_responses, bad_responses
    assert not errors, errors
    print(json.dumps({"gallery_images": total, "browser_errors": errors, "bad_responses": bad_responses, "external_requests": external, "checks": ["search", "empty state", "reset", "pagination", "artist/size filters", "modal", "mobile layout", "slideshow deep link", "navigation", "local-only requests"]}, indent=2))
    browser.close()
