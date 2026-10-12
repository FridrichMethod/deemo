# DEEMO 1 Artwork Archive

**English** · [简体中文](README.zh-CN.md)

[FridrichMethod / deemo](https://github.com/FridrichMethod/deemo) is a personal song artwork collection maintained by FridrichMethod (Zhaoyang Li). It collects public song artwork from the original DEEMO and the related Last Recital / Reborn releases; DEEMO II is out of scope.

The project originates from [mashirozx/deemo](https://github.com/mashirozx/deemo). The upstream history has been squashed into a single initial snapshot; the game textures, the UI design attribution and the Apache-2.0 software license are kept. This version replaces the maintainer links, page information and manifest, and removes the original site's CNAME, Google Analytics and social media links. The repository is public, and the website is published through GitHub Pages.

## Browsing

Online: [searchable Artwork Archive](https://fridrichmethod.github.io/deemo/archive.html) · [slideshow](https://fridrichmethod.github.io/deemo/). The repository, website and artwork are all public; no login is required.

Start a static server that only listens on this machine:

```sh
python -I -m http.server 8765 --bind 127.0.0.1
```

Open [http://127.0.0.1:8765/archive.html](http://127.0.0.1:8765/archive.html) to search song titles, artists and collections, and to filter by source, type and size. Click an image to see the original file, SHA-256, source page and download URL. [index.html](index.html) keeps the original slideshow style, now includes the newly added material, and can also be opened at a specific image from the archive page. Both pages load only local assets, with no analytics or remote font requests.

You can also double-click `archive.html` in the folder to browse offline; the HTTP server is more compatible with browser features such as downloads and slideshow screenshots.

Both pages are in English by default. Each page has a language button (labeled "中文" in the English interface and "English" in the Chinese one) that switches between English and Simplified Chinese. The choice is remembered in the browser (`localStorage`) and in the `?lang=zh-CN` URL parameter; opening an address with `?lang=zh-CN` (e.g. `archive.html?lang=zh-CN`) shows Chinese directly. Switching happens entirely within the page, makes no network requests, and works the same when the files are opened directly (`file://`). Song titles, artist names and metadata quoted from the sources are shown as-is in both languages; only the interface text is translated. The maintainers' notes in the source manifests (file quality, layout, rights and source notes) are written in English and stay in English in the Chinese interface too.

## Website deployment

The site is deployed only from a release: a published [GitHub Release](https://github.com/FridrichMethod/deemo/releases), not marked as a prerelease, of a `vX.Y.Z` tag whose commit is on `main`. Publishing such a Release runs the [Pages workflow](.github/workflows/pages.yml), which deploys that tag's commit, but only after the full [test workflow](.github/workflows/tests.yml) passes on that commit in the same run; if any check fails, nothing is deployed and the live site stays unchanged. A manual run of the Pages workflow on such a tag (`gh workflow run pages.yml --ref v1.1.1`, or "Run workflow" on the Actions page with the tag chosen as the ref) also deploys that tag's commit. Every run, whether a release or a manual run started it, first checks, with one read-only request to the GitHub API, that the tag has a published Release that is not a prerelease. A re-run keeps the event of the run it repeats, so this check also refuses a re-run of a release's run once that Release has been made a draft or a prerelease, or deleted. A re-run that leaves out the Release guard job, such as a re-run of the deploy job alone, deploys nothing, because the deploy job requires the guard job's record of the current attempt; re-run the whole run. Before any test runs, the workflow refuses with an error everything else: a release marked as a prerelease, a tag that is not `vX.Y.Z` (such as `v1.1`, `1.1.0`, `v1.1.0-rc1` or `v01.1.0`), a manual run on a branch (`main` included), a run on a tag without such a Release (no Release, a draft or a prerelease) and a tag whose commit is not on `main`. The job that builds the site checks all of this again before it stages anything, and the deploy job runs only for the commit that these checks approved. Pushes and pull requests never deploy: they only run the test workflow, which runs on every push and every pull request, whatever its base branch. It installs `requirements.txt` and `requirements-lint.txt`, then runs every `tests/test_*.py` under coverage.py (the run fails if they cover less of `scripts/` than `fail_under` in `pyproject.toml`), `scripts/build_catalog.py --verify --check` (a stale `index.html` or catalog fails it), `scripts/build_legacy_inventory.py` and `scripts/fetch_archives.py --verify`; a separate job runs `tests/browser_smoke.py` against a local server with the runner's Chrome, and a lint job runs every pre-commit hook on every file (ruff, mypy, actionlint, ESLint and the whitespace checks; see "Development" below). `.gitattributes` keeps the inherited files and everything under `assets/` out of line-ending conversion, so these byte checks also pass in a Windows checkout (`core.autocrlf=true`).

To release, tag a commit on `main` as `vX.Y.Z` (a `v` and three numbers without leading zeros, nothing else; for example `v1.2.0` or `v1.10.0`, not `v1.02.0`) and publish a GitHub Release of that tag that is not marked as a prerelease. A repository ruleset ("Release tags v*", Settings → Rules → Rulesets) lets only repository admins create, move or delete `v*` tags, and the rules of the `github-pages` environment accept deployments only from tags from `v1.1.1` on (Settings → Environments). A run takes the Pages workflow from the tag's commit, so these rules keep anyone but an admin from deploying through the `github-pages` environment with a workflow of their own. A run on a tag older than `v1.1.1` would use that tag's own workflow, which does not look up the Release, and the environment refuses its deployment. The current release is [v1.1.1](https://github.com/FridrichMethod/deemo/releases/tag/v1.1.1) (2026-10-12).

`scripts/prepare_pages.py` copies only Git-tracked files from `assets/`, `data/`, `src/`, `docs/`, `licenses/` plus an explicit list of root-level web pages, configuration and attribution files; it never publishes `.git/`, workflows, fetch scripts, tests or untracked files. Only a separately generated site directory is uploaded, not the repository root. The allowlist only limits what the website publishes; it is not a sensitive-content detector. The repository itself is public, so check the contents of any file before committing it.

The pages keep `noindex,nofollow`, but that is not access control. The Pages payload has a hard cap of 950 MB, leaving headroom below GitHub Pages' 1 GB limit; if new material exceeds the cap, the deployment stops and the current live version stays unchanged. `tests/test_pages.py` also measures the tracked site against the cap on every push and pull request, so a change that would exceed it fails its checks before it reaches `main`. All original image bytes are kept; assets are not recompressed at publish time.

## Source update check

The [check workflow](.github/workflows/check-sources.yml) runs automatically every Monday at 03:00 UTC and can also be triggered manually from the Actions page, from `main` only (a run from another branch is refused). It re-enumerates the Fandom and BWIKI artwork candidates with `--metadata-only`, without downloading any images, then uses `scripts/check_sources.py` to compare them with `data/sources/wiki-discovery.json` on `main`. If the candidate set has not changed, this is only recorded in the run summary, and a snapshot PR still open from an earlier run is closed with a comment, because its snapshot is now outdated. When files are added, re-uploaded (SHA-1 or dimensions changed) or removed, the workflow pushes the new discovery snapshot and song index to the `auto/wiki-source-check` branch and opens or updates a PR with an English title of the form "Wiki source update: N added · M re-uploaded · K removed (YYYY-MM-DD)". The PR body starts with the English report, which lists each file's source page, dimensions, song title and whether a local download already exists; the full Chinese report follows in a collapsible "简体中文" section. File names, song titles and collection names are written by wiki editors, so the report shows them as inline code: they cannot add links, @mentions, issue references, images or HTML to the PR or the run summary.

Fetching the wikis and installing packages happens in a job with a read-only token and no stored credentials. A second job, which runs no third-party code, receives only the two snapshot files, rebuilds the report from them, commits them and opens the PR; the write token is used only for that push and the PR commands. It updates or closes only the PR opened from `auto/wiki-source-check` in this repository, by the bot or by a maintainer (a PR from a fork with the same branch name is ignored), and it refuses to overwrite that branch if anyone else has committed to it: merge or move those commits, then delete the branch. The bot owns the PR title and body and rewrites them on every update. Closing the PR without merging does not stop the next run that still finds differences from opening a new one; disable the workflow to pause the check. GitHub does not run workflows for a PR created with `GITHUB_TOKEN`, so the test workflow does not run on the snapshot PR; it changes only the two discovery files, which the build does not read, and the test workflow runs every check on `main` after the merge; the next release runs them again before it is deployed.

Merging that PR only updates the snapshot; it does not change the gallery. Afterwards, run `scripts/fetch_wikis.py --resume` locally to download the added and re-uploaded files, refresh the grid previews with `scripts/build_thumbnails.py --prune` (see "Grid previews" below), then build with `scripts/build_catalog.py --verify`, run every `tests/test_*.py`, check the `failures`, `upstream_status` and checksum fields, and commit the images, the previews, the manifests and the rebuilt catalog (commands in the PR body and in "Directory layout and reproduction" below). `--resume` never deletes files: removed and superseded records stay in the manifest, marked with `upstream_status`. Before first use, tick "Allow GitHub Actions to create and approve pull requests" under the repository's Settings → Actions → General → Workflow permissions; otherwise the workflow can push the branch but cannot create the PR. If a public repository has no commits for 60 days, GitHub suspends `schedule` triggers, and the workflow has to be re-enabled on the Actions page.

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

Song titles for the legacy assets are filled in from exact internal keys in a public mapping; a texture with no exact key takes the single key that differs from it only in letter case and is marked `mapped_case_insensitive_internal_key`. A texture that the mapping lacks altogether takes the songs of the wiki uploads of song artwork whose file has the same key, ignoring case, spacing and punctuation (`samsara105_fc` and Fandom's `Samsara105 fc.png`), when they agree, and is marked `mapped_wiki_file_key` (6 textures). For single images in artist posts whose song title cannot be confirmed directly, the original post and page index are kept and the image is marked `unmapped`; a post's grouping is not automatically taken as the collection of the attached images. Composers and artists are kept in separate fields. A collection is shown in one spelling across all sources: the one the wiki records share (see [docs/sources-wikis.md](docs/sources-wikis.md)) or, for a collection no wiki record names, the first of its spellings in code-point order. A record that spells it otherwise (the song mapping's `Etude collection`) keeps its own spelling in `collection_aliases`, which the archive search matches; a post's grouping is quoted as the post spells it.

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
# Every offline test file, as CI runs them (CI also measures their coverage and lints; see "Development")
for test in tests/test_*.py; do .venv/bin/python -I "$test" || echo "FAILED: $test"; done
.venv/bin/python scripts/build_catalog.py --verify --check
.venv/bin/python scripts/build_legacy_inventory.py
.venv/bin/python -I scripts/fetch_archives.py --verify

# Optional: with the local HTTP server above running, the browser acceptance check in an installed Chrome
# (about a minute and a half; it also fails on any console error or failed request)
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -I tests/browser_smoke.py --base http://127.0.0.1:8765 --browser /usr/bin/google-chrome
```

## Development

The code-quality tools are pinned like the build dependencies: ruff, mypy, coverage.py and pre-commit in `requirements-lint.txt`, ESLint in `package.json` and `package-lock.json`, and each pre-commit hook at a full commit SHA in `.pre-commit-config.yaml`. Their settings are in `pyproject.toml` (ruff, mypy, coverage.py) and `eslint.config.mjs`; `tests/test_tooling.py` checks that the pins agree and that every tool skips the byte-pinned and generated files listed at the end of this section. Install them into the virtual environment from above; ESLint needs Node.js 20.19 or a later 20.x, 22.13 or a later 22.x, or 24 or newer (`engines` in `package.json`):

```sh
.venv/bin/python -m pip install -r requirements-lint.txt
npm ci --ignore-scripts
```

`pre-commit run --all-files` runs every hook on every file, as the lint job of the test workflow does: whitespace and file checks, `ruff check`, `ruff format`, mypy, actionlint on the workflows, and ESLint. A hook that has to rewrite a file (trailing whitespace, a missing final newline, formatting) fails the run, so review its change before committing it. `pre-commit install` runs the hooks on every `git commit` in your own clone; it is optional. Each tool also runs on its own:

```sh
.venv/bin/pre-commit run --all-files
.venv/bin/ruff check
.venv/bin/ruff format
.venv/bin/mypy
npx --no-install eslint --max-warnings=0 src
```

ruff applies the first-stage rules in `pyproject.toml` (syntax and runtime errors, pyflakes, pycodestyle warnings, import order, bugbear, pyupgrade and pylint errors) with a line length of 120 and Python 3.10 syntax; `ruff format --check` only reports what `ruff format` would change. mypy is adopted in stages: stage 1 checks, without strict mode, the five build and publication scripts that `[tool.mypy]` in `pyproject.toml` lists (`build_catalog.py`, `build_legacy_inventory.py`, `build_thumbnails.py`, `check_sources.py` and `prepare_pages.py` in `scripts/`); later stages add the fetchers (`scripts/fetch_*.py`), then the tests, then stricter options. Run mypy without arguments so that it checks exactly that list. ESLint only checks, so never run it with `--fix`: `src/` is published exactly as committed and several tests compare parts of its exact text, so findings are fixed by hand in a reviewed change; `src/vendor/` is not linted.

The test workflow runs the unit tests under coverage.py, which also measures every script a test starts as a subprocess, and fails when they cover less of `scripts/` than `fail_under` in `pyproject.toml`. That threshold is the measured baseline, rounded down: raise it when coverage rises, and never lower it to pass. To measure coverage locally, from the repository root:

```sh
for test in tests/test_*.py; do .venv/bin/python -I -m coverage run "$test"; done
.venv/bin/python -I -m coverage combine
.venv/bin/python -I -m coverage report
```

Commits that only reformat code are listed in `.git-blame-ignore-revs`. Run `git config blame.ignoreRevsFile .git-blame-ignore-revs` once in your clone, and `git blame` attributes each line to the change that wrote it rather than to a reformatting.

No tool reads or rewrites the following paths. The global `exclude` of `.pre-commit-config.yaml` skips them for every hook, `extend-exclude` in `pyproject.toml` keeps ruff away from them even when a path is passed explicitly, `eslint.config.mjs` ignores `src/vendor/`, and `.editorconfig` turns off editor normalisation for them.

- The inherited files whose bytes `data/legacy-inventory.json` pins (SHA-256 and Git blob): `LICENSE`, `licenses/DeemoSongs-MIT.txt`, `src/vendor/legacy-ui.js`, `scripts/legacy/*.py`, `assets/legacy/` and `assets/site/`. For the same reason `.gitattributes` exempts them from line-ending conversion.
- The downloaded originals under `assets/public/`, whose SHA-256 the source manifests record.
- Generated files: `index.html`, `data/catalog.json`, `data/catalog.js`, `data/thumbs.json`, `data/legacy-inventory.json` and the previews in `assets/thumbs/`, and the manifests in `data/sources/` that the fetch scripts write. Regenerate them with their scripts instead of editing them by hand.
- The license texts (`LICENSE`, `licenses/*.txt`), kept verbatim, and the third-party bundle `src/vendor/legacy-ui.js`, kept exactly as upstream shipped it.

## Grid previews

The gallery grid shows small derived previews instead of the full originals. Measured on a fresh load of the default view, the first screen fetches 25 previews (0.69 MB) at 1440×900 and 8 previews (0.27 MB) on a 375 px wide phone, where the same cards' originals come to 39 MB and 20 MB; larger desktop windows (1920×1080 and 2560×1440) fetch 36 previews (0.98 MB). `scripts/build_thumbnails.py` writes one WebP preview for each gallery image whose long edge exceeds 480 px (long edge 480 px, quality 75, no EXIF or XMP; an embedded RGB colour profile is kept, a greyscale one is converted to sRGB, and 16-bit greyscale is scaled to 8 bits) to `assets/thumbs/`, named by the first 16 hex digits of the original's SHA-256, and lists them in `data/thumbs.json`; `scripts/build_catalog.py` then adds each preview to its catalog entry as `thumb`. The previews are display copies, not archive files: the originals stay byte-identical, and the viewer, the download link and the link to the original file always serve the original. Smaller images and the PDFs get no preview, and a card falls back to the original if its preview fails to load. The previews (about 33 MB) are published with the site and count toward the 950 MB Pages budget.

After originals are added or removed, rebuild the previews before the catalog, then check them:

```sh
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I tests/test_thumbnails.py
# Optional and slower: re-encode every preview from its original
.venv/bin/python -I scripts/build_thumbnails.py --verify
```

With the Pillow version pinned in `requirements.txt`, a rebuild writes byte-identical files, and `scripts/build_thumbnails.py --verify` re-encodes every preview from its original to confirm it; `scripts/build_catalog.py --verify` only checks that each listed preview exists with its recorded size and SHA-256. `scripts/build_thumbnails.py --check`, which `tests/test_thumbnails.py` also runs, confirms that every larger gallery image has a preview and that each preview exists, decodes, and has the expected size and hash, with no orphaned files. `scripts/build_thumbnails.py --prune` deletes previews whose original has left the gallery. The script will not mix encoder settings or Pillow versions; `scripts/build_thumbnails.py --rebuild` re-encodes every preview. It stops with an error on an original whose colour mode or profile it cannot preview faithfully, such as CMYK, rather than write a preview with wrong colours.

## Attribution

The original interface was designed by Mashiro; this collection and the new tooling are maintained by FridrichMethod. Rights to the song artwork and other game assets remain with Rayark and the respective creators; the repository's code license grants no new license for these media. See [NOTICE](NOTICE), [LICENSE](LICENSE), the [third-party license and asset attribution index](licenses/README.md) and each image's provenance record.

The [license index](licenses/README.md) also covers third-party code and media. The JavaScript bundled with the original interface (`src/vendor/legacy-ui.js`) contains html2canvas, punycode.js and Pace, all MIT-licensed; their license texts are in [html2canvas-MIT.txt](licenses/html2canvas-MIT.txt), [punycode-MIT.txt](licenses/punycode-MIT.txt) and [pace-MIT.txt](licenses/pace-MIT.txt). The index also lists the known creator and license of every inherited icon, font, image and audio file under `assets/site/`, and says where they are unknown. Two commercial fonts inherited from upstream, Copperplate Gothic Light and RocknRoll Typo bold, are no longer distributed because their licenses forbid it; the slideshow uses them only if they are already installed on the visitor's device. Fantique Four, a donationware font, is still included.
