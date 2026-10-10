# Licenses and asset attribution

**English** · [简体中文](README.zh-CN.md)

| Scope | License / attribution entry point |
| --- | --- |
| Project code and original gallery code | The root [LICENSE](../LICENSE) keeps the original Apache-2.0 text; for the maintainer and the original designer, see [NOTICE](../NOTICE). |
| In-game internal keys / song-title mapping | [DeemoSongs-MIT.txt](DeemoSongs-MIT.txt), from syuchan1005/DeemoSongs; the file contents are preserved verbatim. |
| Third-party JavaScript bundled with the original interface | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js) is the upstream `src/main.js`, kept byte for byte. It bundles html2canvas 0.5.0-beta3 (MIT, Copyright (c) 2012 Niklas von Hertzen: [html2canvas-MIT.txt](html2canvas-MIT.txt)), the punycode.js 1.2.4 module built into html2canvas (MIT, Copyright Mathias Bynens: [punycode-MIT.txt](punycode-MIT.txt)) and Pace 1.0.0 (MIT, Copyright (c) 2013 HubSpot, Inc.: [pace-MIT.txt](pace-MIT.txt)). The bundle itself carries only an html2canvas banner with a blank license name and a bare `/*! pace 1.0.0 */` line, so the full license texts are kept here, copied verbatim from the published packages of those versions. |
| Original repository's song artwork and palette-quantized copies | [assets/legacy/](../assets/legacy/); the assets remain attributed to Rayark and the respective creators; for the paired versions, see [data/catalog.json](../data/catalog.json). |
| Icons, backgrounds, fonts, audio, and historical screenshots | [assets/site/](../assets/site/), organized by purpose; the creator and license of every file are listed in the table below, together with the two commercial fonts that were removed. Moving these files to a new directory does not grant them a new Apache license. |
| Newly added public artwork, scans, and reference material | [data/sources/](../data/sources/) records the specific authors, original posts, file URLs, and source status. |

The root `LICENSE` and `NOTICE` are the standard software-license and attribution entry points for the whole repository; this directory collects the third-party license texts and the scope index.

[data/legacy-inventory.json](../data/legacy-inventory.json) records each file's old path, new path, pre-migration Git blob, byte count, and SHA-256, covering all of the old song artwork, the site media, the preserved old tools, and the license texts; the two removed fonts are no longer listed.

## Inherited site media (assets/site/)

Every file below comes from the upstream snapshot of [mashirozx/deemo](https://github.com/mashirozx/deemo); "Inherited from" is its `original_path` in the legacy inventory. Upstream recorded no source or license for any of them, so the creators were identified from the files themselves (embedded font names, ID3 tags, SVG metadata, image content) and from the committed wiki song data. Where a creator or license is still unknown, the table says so. The Apache-2.0 LICENSE does not cover any of these files.

| File | Inherited from | Used by | Creator / rights holder | License and notes |
| --- | --- | --- | --- | --- |
| `audio/Riin - Alice good night.mp3` | `src/Riin - Alice good night.mp3` | Slideshow background music | "Alice good night", a DEEMO song (Epilogue collection) that the DEEMO wikis credit to Sleepy WiFi feat. Riin; © Rayark Inc. and/or the artists (Sleepy WiFi, Riin), not verified. | No license known. Its ID3 tags (artist "Riin", album "最新热歌慢摇102", a NetEase Cloud Music "163 key" comment) point to a music-streaming download rather than an official release. Whether the recording is the in-game track has not been checked. |
| `fonts/Fantique-Four.ttf` | `src/Fantique-Four.ttf` | Slideshow display font (`Fantique`) | Steve Tune, Digital Empires (Copyright Digital Empires Inc 1997) | Shareware/donationware. The license embedded in the font says "The font is complete as is and you may use it"; it does not mention redistribution. |
| `icons/android-chrome-192x192.png` | `android-chrome-192x192.png` | Web app manifest | DEEMO app-icon artwork (Deemo and the girl), © Rayark Inc. | No license. A resized copy of the game's icon; who made the icon set, and from which image, was not recorded. |
| `icons/apple-touch-icon.png` | `apple-touch-icon.png` | Slideshow touch icon and `og:image` | DEEMO app-icon artwork, © Rayark Inc. | As above. |
| `icons/favicon-16x16.png` | `favicon-16x16.png` | Slideshow favicon | DEEMO app-icon artwork, © Rayark Inc. | As above. |
| `icons/favicon-32x32.png` | `favicon-32x32.png` | Slideshow favicon | DEEMO app-icon artwork, © Rayark Inc. | As above. |
| `icons/favicon.ico` | `favicon.ico` | Archive page favicon | DEEMO app-icon artwork, © Rayark Inc. | As above. |
| `icons/mstile-150x150.png` | `mstile-150x150.png` | Windows tile (`browserconfig.xml`) | DEEMO app-icon artwork, © Rayark Inc. | As above. |
| `icons/safari-pinned-tab.svg` | `safari-pinned-tab.svg` | Slideshow Safari pinned-tab icon | Unknown. A plain rounded-square silhouette; its metadata says it was traced with potrace 1.11. | Unknown. |
| `icons/camera.svg` | `src/camera.svg` | Slideshow screenshot button | Unknown. The path data looks like the "photo camera" icon from Google's Material Design icons (Apache-2.0); not confirmed. | Unknown. |
| `icons/github.svg` | `src/github.svg` | Slideshow GitHub link | GitHub mark, a logo and trademark of GitHub, Inc.; the drawing's author is unknown. | Unknown; used only to link to GitHub. |
| `icons/love.svg` | `src/love.svg` | Not used | Unknown (heart). | Unknown. |
| `icons/pause.svg` | `src/pause.svg` | Slideshow music button | Unknown (TV-shaped pause icon). | Unknown. |
| `icons/play.svg` | `src/play.svg` | Slideshow music button | Unknown (TV-shaped play icon). | Unknown. |
| `icons/rotate.svg` | `src/rotate.svg` | Not used | Unknown (screen rotation). | Unknown. |
| `icons/sand-clock.svg` | `src/sand-clock.svg` | Slideshow autoplay button | Unknown (hourglass). | Unknown. |
| `icons/save.svg` | `src/save.svg` | Slideshow screenshot save button | Unknown (floppy disk). | Unknown. |
| `icons/weibo.svg` | `src/weibo.svg` | Not used | Weibo logo, a trademark of Weibo Corporation; the drawing's author is unknown. | Unknown. |
| `icons/wordpress.svg` | `src/wordpress.svg` | Not used | WordPress logo, a trademark of the WordPress Foundation; the drawing's author is unknown. | Unknown. |
| `images/bg.png` | `src/bg.png` | Slideshow page background | Unknown (grey paper texture). | Unknown. |
| `images/upstream-preview-lower.png` | `src/Capture.png` | Not used | Screenshot of Mashiro's upstream gallery (deemo.shino.cc): the page design is Mashiro's; the artwork shown is © Rayark Inc. and its artists. | Kept as a historical record; byte-identical to `upstream-preview-upper.png`. |
| `images/upstream-preview-upper.png` | `src/Capture.PNG` | Not used | The same screenshot. | As above. |

The SVG icons record tools, not authors. `github`, `love`, `rotate`, `sand-clock`, `save`, `weibo` and `wordpress` use the layer id `Capa_1` of Adobe Illustrator exports, and `save` and `sand-clock` also have the `data-original` attributes of an online icon editor; such markers are common in icons downloaded from Flaticon, but no source has been confirmed. `pause` and `play` have the `t` and `p-id` attributes typical of iconfont.cn downloads, with timestamps from April 2018. Information about any of these sources is welcome as an issue.

### Removed fonts

| File | Inherited from | Creator / rights holder | Why it was removed |
| --- | --- | --- | --- |
| `fonts/COPRGTL.ttf` (removed) | `src/COPRGTL.ttf` | Copperplate Gothic Light: data copyright URW Software & Type GmbH, additional data copyright The Font Bureau, Inc., Copyright 1994 Microsoft Corporation | A commercial font, all rights reserved; nothing permits redistributing it. |
| `fonts/RocknRoll_Typo_bold.ttf` (removed) | `src/RocknRoll_Typo_bold.ttf` | RocknRoll Typo bold: Copyright (c) 2010 by Otto Maurer, sold through MyFonts | The license embedded in the font forbids making it available to any third party. |

Both files were removed in 2026 and are no longer in the legacy inventory or on the website. The slideshow keeps their CSS family names and uses the fonts only when a visitor already has them installed (CSS `local()`); everyone else sees the fallback fonts.
