# DEEMO 1 Artwork Archive

**English** · [简体中文](README.zh-CN.md)

[FridrichMethod / deemo](https://github.com/FridrichMethod/deemo) is a personal song artwork collection maintained by FridrichMethod (Zhaoyang Li). It collects public song artwork from the original DEEMO and the related Last Recital / Reborn releases; DEEMO II is out of scope.

The project originates from [mashirozx/deemo](https://github.com/mashirozx/deemo). The upstream history has been squashed into a single initial snapshot; the game textures, the UI design attribution and the Apache-2.0 software license are kept. This version replaces the maintainer links, page information and manifest, and removes the original site's CNAME, Google Analytics and social media links. The repository is public, and the website is published through GitHub Pages.

## Browsing

Online: [searchable gallery](https://fridrichmethod.github.io/deemo/archive.html) · [slideshow](https://fridrichmethod.github.io/deemo/). The repository, website and artwork are all public; no login is required.

Start a static server that only listens on this machine:

```sh
python -I -m http.server 8765 --bind 127.0.0.1
```

Open [http://127.0.0.1:8765/archive.html](http://127.0.0.1:8765/archive.html) to search song titles, artists and collections, and to filter by source, type and size. Click an image to see the original file, SHA-256, source page and download URL. [index.html](index.html) keeps the original slideshow style, now includes the newly added material, and can also be opened at a specific image from the archive page. Both pages load only local assets, with no analytics or remote font requests.

You can also double-click `archive.html` in the folder to browse offline; the HTTP server is more compatible with browser features such as downloads and slideshow screenshots.

Both pages are in English by default. Each page has a language button (labeled "中文" in the English interface and "English" in the Chinese one) that switches between English and Simplified Chinese. The choice is remembered in the browser (`localStorage`) and in the `?lang=zh-CN` URL parameter; opening an address with `?lang=zh-CN` (e.g. `archive.html?lang=zh-CN`) shows Chinese directly. Switching happens entirely within the page, makes no network requests, and works the same when the files are opened directly (`file://`). Song titles, artist names and metadata quoted from the sources are shown as-is in both languages; only the interface text is translated. The maintainers' notes in the source manifests (file quality, layout, rights and source notes) are written in English and stay in English in the Chinese interface too.

## Website deployment

After a push to `main`, the [Pages workflow](.github/workflows/pages.yml) publishes the site automatically, but only after the [test workflow](.github/workflows/tests.yml) passes in the same run; if any check fails, nothing is deployed and the live site stays unchanged. The test workflow also runs on every push and every pull request, whatever its base branch. It installs `requirements.txt` and runs every `tests/test_*.py`, `scripts/build_catalog.py --verify --check` (a stale `index.html` or catalog fails it), `scripts/build_legacy_inventory.py` and `scripts/fetch_archives.py --verify`; a separate job runs `tests/browser_smoke.py` against a local server with the runner's Chrome. `.gitattributes` keeps the inherited files and everything under `assets/` out of line-ending conversion, so these byte checks also pass in a Windows checkout (`core.autocrlf=true`).

`scripts/prepare_pages.py` copies only Git-tracked files from `assets/`, `data/`, `src/`, `docs/`, `licenses/` plus an explicit list of root-level web pages, configuration and attribution files; it never publishes `.git/`, workflows, fetch scripts, tests or untracked files. Only a separately generated site directory is uploaded, not the repository root. The allowlist only limits what the website publishes; it is not a sensitive-content detector. The repository itself is public, so check the contents of any file before committing it.

The pages keep `noindex,nofollow`, but that is not access control. The Pages payload has a hard cap of 950 MB, leaving headroom below GitHub Pages' 1 GB limit; if new material exceeds the cap, the deployment stops and the current live version stays unchanged. All original image bytes are kept; assets are not recompressed at publish time.

## Source update check

The [check workflow](.github/workflows/check-sources.yml) runs automatically every Monday at 03:00 UTC and can also be triggered manually from the Actions page, from `main` only (a run from another branch is refused). It re-enumerates the Fandom and BWIKI artwork candidates with `--metadata-only`, without downloading any images, then uses `scripts/check_sources.py` to compare them with `data/sources/wiki-discovery.json` on `main`. If the candidate set has not changed, this is only recorded in the run summary, and a snapshot PR still open from an earlier run is closed with a comment, because its snapshot is now outdated. When files are added, re-uploaded (SHA-1 or dimensions changed) or removed, the workflow pushes the new discovery snapshot and song index to the `auto/wiki-source-check` branch and opens or updates a PR with an English title of the form "Wiki source update: N added · M re-uploaded · K removed (YYYY-MM-DD)". The PR body starts with the English report, which lists each file's source page, dimensions, song title and whether a local download already exists; the full Chinese report follows in a collapsible "简体中文" section. File names, song titles and collection names are written by wiki editors, so the report shows them as inline code: they cannot add links, @mentions, issue references, images or HTML to the PR or the run summary.

Fetching the wikis and installing packages happens in a job with a read-only token and no stored credentials. A second job, which runs no third-party code, receives only the two snapshot files, rebuilds the report from them, commits them and opens the PR; the write token is used only for that push and the PR commands. It updates or closes only the PR opened from `auto/wiki-source-check` in this repository, by the bot or by a maintainer (a PR from a fork with the same branch name is ignored), and it refuses to overwrite that branch if anyone else has committed to it: merge or move those commits, then delete the branch. The bot owns the PR title and body and rewrites them on every update. Closing the PR without merging does not stop the next run that still finds differences from opening a new one; disable the workflow to pause the check. GitHub does not run workflows for a PR created with `GITHUB_TOKEN`, so the test workflow does not run on the snapshot PR; it changes only the two discovery files, which the build does not read, and the Pages workflow runs every check again after the merge.

Merging that PR only updates the snapshot; it does not change the gallery. Afterwards, run `scripts/fetch_wikis.py --resume` locally to download the added and re-uploaded files, then build, verify, check the `failures`, `upstream_status` and checksum fields, and commit the images and the manifest (commands in the PR body and in "Directory layout and reproduction" below). `--resume` never deletes files: removed and superseded records stay in the manifest, marked with `upstream_status`. Before first use, tick "Allow GitHub Actions to create and approve pull requests" under the repository's Settings → Actions → General → Workflow permissions; otherwise the workflow can push the branch but cannot create the PR. If a public repository has no commits for 60 days, GitHub suspends `schedule` triggers, and the workflow has to be re-enabled on the Actions page.

The Pixiv artwork IDs for the artist source (`fetch_artists.py`) are written into the script, and the public archive sources are largely static content, so neither is covered by the automatic check; adding new works still requires editing the script by hand and fetching again.

## Curated sources

| Source | Contents and notes |
| --- | --- |
| [Artists (original posts)](docs/sources-artists.md) | 86 public uploads: ころころさん, SnowEgg, Ryori, K@I, Blaze Wu, Siyouko. Includes song artwork, collection covers, long contact sheets and separately labeled process references. The largest single song artwork is 3030×3030. |
| [Wiki](docs/sources-wikis.md) | 809 images: Fandom 561, BWIKI 248. Includes original-game song/collection images, different versions and texture atlases; the actually downloaded files and the results of verification against the original sites' metadata are kept. The illustrator directory of a Japanese wiki is saved as well. |
| [Public archives](docs/sources-archives.md) | 189 historical Tumblr covers, 19 OST packaging/booklet scans, 4 official website illustrations, 2 official reference PDFs. Low-resolution reposts and scans are categorized separately. |
| Original repository | The 320 assets in `assets/legacy/trans/` are used for display; the 320 palette-quantized copies in `assets/legacy/tiny/` serve as paired versions, offered as downloads in the original image's details. |

On 2026-09-05, 1,057 public source files were archived in total; on 2026-10-08, 52 BWIKI collection cover thumbnails found by the source check were added, for a total of 1,109 public source files. The original repository's 320 images and their 320 palette-quantized copies are kept as well. The quantized copies are recorded in `variants` and are not counted a second time in the gallery's image count. [data/catalog.json](data/catalog.json) is authoritative for the exact deduplicated counts and the retrieval status. Image counts include different versions of the same song, collection covers and reference material, **so they do not equal the number of distinct songs, nor do they mean that a master has been collected for every song**. Byte-identical files are shown once while all their sources are kept; no version was blindly overwritten based on dimensions or file size.

Every download keeps the original bytes of the response, with no AI upscaling, cropping, watermark removal, background removal or format conversion. `original` means the original download variant offered by the site; it is not automatically the same as the artist's working master. Some Fandom downloads have checksums that differ from the wiki upload metadata; the manifest records this explicitly.

Song titles for the legacy assets are filled in from exact internal keys in a public mapping; a texture with no exact key takes the single key that differs from it only in letter case and is marked `mapped_case_insensitive_internal_key`. For single images in artist posts whose song title cannot be confirmed directly, the original post and page index are kept and the image is marked `unmapped`; a post's grouping is not automatically taken as the collection of the attached images. Composers and artists are kept in separate fields.

For paid art books and games, only the purchase source is recorded; they were not downloaded. Dead share links, failed accesses, audio waveforms/spectrograms, videos and mixed fanart sites whose attribution cannot be confirmed are recorded in the source inventory and are not counted as successfully downloaded artwork.

## Directory layout and reproduction

```text
assets/
  public/{artists,wikis,archives}/  newly added public material and references
  legacy/trans/                    original repository's unquantized transparent PNGs
  legacy/tiny/                     paired historical palette-quantized copies
  thumbs/                          derived WebP grid previews (not archive files)
  site/{icons,fonts,images,audio}/  icons, fonts, backgrounds/screenshots, audio
src/                               page JS/CSS; vendor/ holds the original third-party bundle
src/i18n.js / src/i18n/            language switch script and English/Chinese UI strings
templates/slideshow.html           slideshow template (generates the root index.html)
scripts/                           fetching, building and verification; legacy/ holds historical experiment scripts
data/sources/                      per-source manifests, candidates and song-title mapping
data/catalog.{json,js}              full provenance records and the offline browsing catalog
data/legacy-inventory.json          migration paths, Git blobs and SHA-256 of 668 legacy files
licenses/                          third-party license texts and asset attribution index
*.zh-CN.md                         Simplified Chinese versions of README, docs/ and other documents; the same-name .md is the default English version
LICENSE / NOTICE                   standard software license and attribution entry points
archive.html / index.html          the two static web page entry points
site.webmanifest / browserconfig.xml  browser configuration entry points
```

Python 3.10 or newer is required (the pinned Pillow and requests need it). Install the dependencies and build:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/build_catalog.py --verify
```

Refresh the public sources as needed, or resume the saved wiki candidates:

```sh
.venv/bin/python -I scripts/fetch_artists.py --workers 4
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python -I scripts/fetch_archives.py
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
```

To rediscover wiki entries, drop `--resume`. Network sources may be rate-limited or go offline; check `failures`, `status` and `access_status` in each manifest, and the Git diff, before committing an update. The specific limitations of each source script are described in the corresponding document in the table above. All builds go through `scripts/build_catalog.py`; the old root-level `html.py` entry point has been removed. The upstream experiment scripts in `scripts/legacy/` are kept only for historical reference and are not part of the current workflow.

Offline verification and browser acceptance checks:

```sh
.venv/bin/python -I tests/test_catalog.py
.venv/bin/python -I tests/test_layout.py
.venv/bin/python -I tests/test_pages.py
.venv/bin/python -I tests/test_i18n.py
.venv/bin/python -I tests/test_check_sources.py
.venv/bin/python scripts/build_catalog.py --verify --check
.venv/bin/python scripts/build_legacy_inventory.py
.venv/bin/python -I scripts/fetch_archives.py --verify

# Optional: with the local HTTP server running, run the browser acceptance check with an installed Chrome
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -I tests/browser_smoke.py --browser /usr/bin/google-chrome
```

## Grid previews

The gallery grid shows small derived previews instead of the full originals, so the first view loads well under 1 MB of images instead of tens of megabytes. `scripts/build_thumbnails.py` writes one WebP preview for each gallery image whose long edge exceeds 480 px (long edge 480 px, quality 75, no EXIF or XMP; an embedded RGB colour profile is kept, a greyscale one is converted to sRGB, and 16-bit greyscale is scaled to 8 bits) to `assets/thumbs/`, named by the first 16 hex digits of the original's SHA-256, and lists them in `data/thumbs.json`; `scripts/build_catalog.py` then adds each preview to its catalog entry as `thumb`. The previews are display copies, not archive files: the originals stay byte-identical, and the viewer, the download link and the link to the original file always serve the original. Smaller images and the PDFs get no preview, and a card falls back to the original if its preview fails to load. The previews (about 33 MB) are published with the site and count toward the 950 MB Pages budget.

After originals are added or removed, rebuild the previews before the catalog, then check them:

```sh
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I tests/test_thumbnails.py
```

With the Pillow version pinned in `requirements.txt`, a rebuild writes byte-identical files, and `--verify` re-encodes every preview from its original to confirm it. `--check`, which `tests/test_thumbnails.py` also runs, confirms that every larger gallery image has a preview and that each preview exists, decodes, and has the expected size and hash, with no orphaned files. `--prune` deletes previews whose original has left the gallery. The script will not mix encoder settings or Pillow versions; `--rebuild` re-encodes every preview. It stops with an error on an original whose colour mode or profile it cannot preview faithfully, such as CMYK, rather than write a preview with wrong colours.

## Attribution

The original interface was designed by Mashiro; this collection and the new tooling are maintained by FridrichMethod. Rights to the song artwork and other game assets remain with Rayark and the respective creators; the repository's code license grants no new license for these media. See [NOTICE](NOTICE), [LICENSE](LICENSE), the [third-party license and asset attribution index](licenses/README.md) and each image's provenance record.

The [license index](licenses/README.md) also covers third-party code and media. The JavaScript bundled with the original interface (`src/vendor/legacy-ui.js`) contains html2canvas, punycode.js and Pace, all MIT-licensed; their license texts are in [html2canvas-MIT.txt](licenses/html2canvas-MIT.txt), [punycode-MIT.txt](licenses/punycode-MIT.txt) and [pace-MIT.txt](licenses/pace-MIT.txt). The index also lists the known creator and license of every inherited icon, font, image and audio file under `assets/site/`, and says where they are unknown. Two commercial fonts inherited from upstream, Copperplate Gothic Light and RocknRoll Typo bold, are no longer distributed because their licenses forbid it; the slideshow uses them only if they are already installed on the visitor's device. Fantique Four, a donationware font, is still included.
