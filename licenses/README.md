# Licenses and asset attribution

**English** · [简体中文](README.zh-CN.md)

| Scope | License / attribution entry point |
| --- | --- |
| Project code and original gallery code | The root [LICENSE](../LICENSE) keeps the original Apache-2.0 text; for the maintainer and the original designer, see [NOTICE](../NOTICE). |
| In-game internal keys / song-title mapping | [DeemoSongs-MIT.txt](DeemoSongs-MIT.txt), from syuchan1005/DeemoSongs; the file contents are preserved verbatim. |
| Third-party JavaScript bundled with the original interface | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js) keeps the original bundle and its embedded attributions; the upstream header contains a license field that was left blank, and this migration did not infer a value to fill it in. |
| Original repository's song artwork and palette-quantized copies | [assets/legacy/](../assets/legacy/); the assets remain attributed to Rayark and the respective creators; for the paired versions, see [data/catalog.json](../data/catalog.json). |
| Icons, backgrounds, fonts, audio, and historical screenshots | [assets/site/](../assets/site/), organized by purpose; for the existing attribution notes, see NOTICE; moving these files to a new directory does not grant them a new Apache license. |
| Newly added public artwork, scans, and reference material | [data/sources/](../data/sources/) records the specific authors, original posts, file URLs, and source status. |

The root `LICENSE` and `NOTICE` are the standard software-license and attribution entry points for the whole repository; this directory collects the third-party license texts and the scope index.

[data/legacy-inventory.json](../data/legacy-inventory.json) records each file's old path, new path, pre-migration Git blob, byte count, and SHA-256, covering all of the old song artwork, the site media, the preserved old tools, and the license texts.
