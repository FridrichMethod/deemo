# Public archives and official reference material

**English** · [简体中文](sources-archives.zh-CN.md)

This directory corresponds to `data/sources/archives.json`; the fetch script is `scripts/fetch_archives.py`. Files keep the original bytes of the server response, with no resizing, transcoding, sharpening or AI upscaling. File extensions follow the actual decoded format, and each image records its page, download URL, final URL, dimensions, SHA-256, byte count and fetch time.

On 2026-09-05 the fetch actually retrieved **214 files, 51.2 MB**: 212 images and 2 PDFs. This covers DEEMO 1 and the related older Last Recital songs; DEEMO II is not collected.

| Source | Downloaded | Observed content and scope |
|---|---:|---|
| [Cover Art Archive](https://musicbrainz.org/release/fb7dec3c-01cd-4659-a398-e4494b824db4/cover-art) | 19 | Scans of the OST packaging and booklet pages, as originally uploaded. The cover is 2000²; booklet pages are mostly about 2180×1100. Two-page spreads, crops and scanning artifacts are kept; classified as reference. Not independent song-artwork masters. |
| [Tunes of Rayark](https://rayarkmusic.tumblr.com/deemo) | 189 | The tags for all 32 collections plus the Alice/Celia tags were paged through to the end: 189 song posts in total. 164 images are 500², the rest are smaller; classified as community_repost. The original posts may contain typography, game backgrounds or secondary retouching, so they cannot be treated as a preferred source for high-resolution replacements. The collection 30 and 31 tags currently have 0 posts. |
| [DEEMO official website](https://deemo.com/) | 4 | Character/scene key art from the official website, about 798–985px wide and 901–1431px tall; classified as illustration. The official website has no download library covering all song artwork. |
| [English exhibition guide](https://rayark.promo/deemo_exhibition_en.pdf) | 1 | Official PDF; reference; kept out of the song-artwork slideshow. |
| [Rayark Game Brand Assets](https://rayark.promo/rayark_site/RAYARK_GameBrandAssets.pdf) | 1 | Official brand-material PDF; reference. |

The [Tunes of Rayark FAQ](https://rayarkmusic.tumblr.com/faq) states explicitly that the blog is not official, and says the author made cleaned artwork. For the [Kitsunefreak `my edit` tag](https://kitsunefreak.tumblr.com/tagged/my%20edit) that the FAQ points to, the public API currently returns 0 posts; the empty result is not recorded as a successful retrieval of edited images. Only the static cover images in song posts are fetched here; no music was downloaded.

How each of the other sources was handled is recorded in the JSON and can be looked up by `status` / `access_status`:

- The official KADOKAWA/BookWalker art book and Steam Reborn are marked `purchase_required`; nothing was purchased, and neither the game nor the paid book was downloaded.
- The Reddit 3.4/4.x/5.x threads are only historical sharing leads: evidence from the previous search points to Wikia re-hosts or upscaled images, and to shares that have since gone offline. A discussion page that still opens does not mean the shared files are still alive.
- The [historical Dropbox](https://www.dropbox.com/sh/qdzxn3menwuk5he/AAAL2v6303lMlAf1ma54tJjga?dl=0) share returned HTTP 200 when tested, but the page content is `Dropbox - Error`; no shared files were obtained.
- The original repository's [extraction-process blog post](https://2heng.xin/2018/04/05/python-pil/) and the old Baidu shares are kept as provenance. The Baidu pages open, but anonymous direct file download was not verified, and the old unpacked assets are not misrepresented as a new high-resolution source.
- The [Internet Archive 202606 item](https://archive.org/details/deemo_ost-_202606) lists 443 FLAC, 443 MP3, 443 PNG and 443 spectrogram files. Sampled ordinary PNGs are actually 800×200 audio waveforms, and the audio descriptions also attribute the embedded cover art to DEEMO Wiki. The audio and the waveform/spectrogram images are therefore not downloaded.
- Zerochan/Safebooru are indexes that mix fanart, official images, crops and re-uploads. No independent new official song artwork was verified there, and the sites' images were not bulk-imported as masters.
- The third-party Tumgik mirror and SteamDB currently return HTTP 403. The Blaze Wu works that the former points to are fetched from the original Tumblr pages by the artist-source importer.
- The Japanese DEEMO Wiki illustrator directory belongs to the wiki importer (`wikis:wikiwiki-illustrators`); its entry here is marked `delegated`.
- The Bilibili video search is kept as a lead. Video frames have gone through video encoding, so they are not used as a source of original standalone images.

Whether these sources are accessible and their copyright status are two separate matters. Attribution to the original authors and to Rayark is kept here; only public sources are recorded, and neither the maintainer's identity nor the repository's code license is applied to the artwork.

To reproduce the fetch and verify offline:

```sh
python -I scripts/fetch_archives.py
python -I scripts/fetch_archives.py --verify
```

Requires Python 3.10 or newer; dependencies are `requests`, `beautifulsoup4` and `Pillow`; `-I` runs Python in isolated mode. The fetcher sends requests in parallel on 3–5 threads and writes any failure to `failures` under the real source ID and URL; a source with failures is marked `partial` or `failed`, and the command exits with a non-zero status. Each Tumblr tag is paginated according to the API's `posts-total`, with no silent truncation cap. Downloads and page reads go only to the expected hosts (Cover Art Archive/archive.org, Tumblr, deemo.com, rayark.promo), and every redirect is checked against them; the reachability checks of the other catalogued pages only record the HTTP status and final URL, and nothing is saved from them. Files are written after their content is verified; if the remote content changes, the old file is kept and the new version is saved with a hash suffix. The manifest is written once, atomically, at the end of a run. A rerun never drops a verified record or deletes a file: the new version of a changed file keeps the asset ID and the old record stays as `<ID>:<first 12 hex digits of its SHA-256>` with `upstream_status: superseded`, and a record that is not reproduced is kept as `fetch_failed` or `removed`. If upstream later reverts to an earlier version, that version is the current record again and is not listed twice. The one exception is an archived file that is missing from the checkout: the rerun saves the current upstream bytes at its path, and the old record, whose bytes are gone, is dropped.
