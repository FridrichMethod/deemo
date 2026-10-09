# Public sources from the artists

**English** · [简体中文](sources-artists.zh-CN.md)

This batch holds 86 images from 18 public artwork pages posted by the artists, and also records 1 career-history page as a reference. The retrieval time, original download URL, page URL, width and height, actual file format, SHA-256 and author information are all in [`data/sources/artists.json`](../data/sources/artists.json). Every image keeps the original bytes as downloaded: nothing was resized, cropped, color-adjusted, watermark-stripped or re-encoded.

| Artist / source | Files | Actual downloaded dimensions and contents |
| --- | ---: | --- |
| [ころころさん / 鳩紫ぽっぽ](https://kolokolsan.jimdofree.com/仕事の制作物/) | 5 | 3 at 3030×3030; Matricaria 3030×2200; in a cradle 2048×1662 |
| [SnowEgg / 雪歌草](https://www.pixiv.net/en/users/1755315/illustrations/Deemo) | 39 | 32 song artworks at about 1236×1240–1241; 7 collection covers at 928×928 |
| [Ryori](https://www.pixiv.net/en/users/1400721/illustrations/Deemo) | 20 | 10 song artworks about 1324–1332px wide; 2 collection covers; 8 design/process references |
| [K@I](https://www.pixiv.net/en/users/93444/illustrations/Deemo) | 8 | 5 song-artwork presentations 1500px wide; 3 long contact sheets, the largest 1417×6288 |
| [Blaze Wu](https://wublaze.tumblr.com/tagged/deemo) | 8 | 5 song artworks and 1 collection cover at 1280×960; 2 long contact sheets at 512×1920 |
| [Siyouko / 硝子](https://www.pixiv.net/en/artworks/47910628) | 6 | A public compilation of DEEMO illustrations, 359–886px; not a high-resolution song-artwork pack, and not claimed to be entirely commissioned in-game song artwork |

By purpose: 57 song artworks, 10 collection covers, 5 long contact sheets, 8 design/process references and 6 other DEEMO illustrations. The classification is based on the context of the original posts and on visual inspection. Decorative frames, copyright text, drafts and watermarks that came with the original posts are all retained.

## Original uploads and platform previews

Pixiv images are fetched by reading `urls.original` from the public `/ajax/illust/{id}` and `/pages` metadata, which require no login, and downloads send an ordinary page Referer. No account, cookie, login credential or paid content was used.

The Jimdo page's lightbox labels some PNGs as 2048px; the site's public `/transf/none/` image path actually returns the 3030px original files. The manifest also stores the lightbox URL and the metadata dimensions published on the page, so `source_dimensions` may be smaller than the decoded `width`/`height`.

Tumblr images come from the largest image URL published by the author's public legacy API. The `1280` here is the platform's size-tier name. It does not guarantee that every image is actually 1280 wide, nor that the artist's working master was obtained; the two early long contact sheets are actually only 512px wide.

Siyouko's [career page](https://princeofglass.blogspot.com/p/blog-page_1146.html) confirms their DEEMO song artwork, cutscene and character-design work, but the page has no downloadable DEEMO images, so it is recorded as `reference_only`. The DEEMO tag lists of the Pixiv authors above were checked. Collaboration art for unrelated games, mixed anniversary pieces and other posts outside the scope of commissioned song artwork were kept out of this batch.

## Files and song titles

Images are in `assets/public/artists/{artist}/`. Pixiv/Tumblr file names keep the post ID and the zero-based page index; Jimdo files use the song titles the author explicitly labeled.

When a song title cannot be confirmed directly from the original post, `song_titles` stays an empty array and `mapping_status` is `unmapped`. `collection` is the original post's collection grouping; it does not mean that every attached image belongs to that collection. In particular, Ryori attaches several collection covers and process images to the same post. Each long contact sheet stays a single file, with no guessed crop boundaries.

## Reproduction

Requires Python 3, `requests`, `beautifulsoup4` and `Pillow`. Run from the repository root:

```sh
python scripts/fetch_artists.py --workers 4
```

A rerun checks the public page metadata and reuses a local file when its SHA-256 is correct and its download URL is unchanged. Cached images are downloaded again only when `--refresh` is added explicitly. The script verifies that every image decodes completely, chooses the file extension from the actual format, deduplicates by SHA-256, writes every failure to the manifest and returns a non-zero exit status if there are any.

The download and the independent verification for this batch both succeeded 86/86, with 0 failures; the original images total 107,043,058 bytes. Copyright in the images remains with Rayark and the respective creators; this repository's software license grants no new license to these images.
