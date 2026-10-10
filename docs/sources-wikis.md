# DEEMO 1 public wiki artwork sources

**English** · [简体中文](sources-wikis.zh-CN.md)

This directory records the public song artwork from the Fandom DEEMO Wiki and the Bilibili DEEMO Wiki. The downloads cover the original DEEMO and its Last Recital / Reborn ports; DEEMO II, audio, and charts are not included.

Import result on 2026-09-05: **all 757 / 757 candidate images succeeded, with 0 final failures**, totaling **457,462,452 bytes** (about 436.27 MiB). Fandom supplied 561 images and BWIKI 196; of these, 662 are song artwork and 95 are collection covers; the actual formats are 756 PNG and 1 JPEG.

A full audit checked each file's byte count, SHA-256, SHA-1, and actual format and dimensions as read by Pillow, and confirmed that the manifest's unique IDs and the files on disk correspond one to one; all 757 passed, with no orphaned files. 753 match the original-upload SHA-1 published by the wiki. 4 differ: `Spring Snowflake Flower`, `Ark of Desire`, `I Race The Dawn x Sunset`, and `Protest`; all of them keep their mismatch flags. 7 were retrieved through the full-size `format=png` fallback, and their SHA-1 values also all ended up matching the original uploads.

Incremental update on 2026-10-08: the source-check workflow found 57 BWIKI collection cover candidates (all uploaded in 2021; they reflect a difference in the `allimages` enumeration rather than newly published artwork). They were imported with `--resume`, and all of them match the wiki's original-upload SHA-1; in addition, the `collections`/`related_pages` of 11 existing records were updated along with the new candidate metadata. Five of the 57, the 70×47 `* Collections Titletab` images, are UI title tabs rather than covers: BWIKI discovery did not yet apply the UI filter used for Fandom. They were removed from the snapshot, the manifest and the repository on 2026-10-09, and discovery now excludes them. The remaining 52 are thumbnails of about 180×180. The total is now **809 images** (Fandom 561, BWIKI 248; song artwork 662, collection covers 147), **459,809,756 bytes** in all (about 438.51 MiB), 808 PNG and 1 JPEG; the SHA-1 mismatches are still the same 4 images listed above.

## Files and sources

- `assets/public/wikis/fandom/`: image references are taken from the song pages in the original DEEMO's `Category:Songs` and from the original DEEMO's collection pages.
- `assets/public/wikis/bwiki/`: every page of `allimages` is enumerated and image names are matched against the normalized titles in the song index; collection covers that can be confirmed are included as well. UI images (title tabs, logos, screenshots, DEEMO II) are excluded with the same filter as on Fandom, and collection covers from either wiki need a short edge of at least 100 px.
- `data/sources/wikis.json`: the final image inventory, download status, and failure records; it is the input to the gallery integration.
- `data/sources/wiki-discovery.json`: the candidate snapshot, which can be re-checked and used to resume downloading; it contains the raw MediaWiki imageinfo.
- `data/sources/wiki-song-index.json`: titles, collections, composers, and image references from the public song pages. The template field `Artist` is the composer; it is stored as `composer` and is not mistaken for the illustrator.
- `data/sources/wiki-illustrator-index.json`: 479 heading/table rows from the Japanese wiki's directory, saved in their original page order. The page contains composer and Vocalist sections as well as a separate `illustrator` section; the last of these lists 9 illustrators, and the other sections are not treated as illustrator attribution.
- `data/sources/song-mapping.json`: legacy in-game internal keys and song information from [syuchan1005/DeemoSongs](https://github.com/syuchan1005/DeemoSongs); its [MIT license](../licenses/DeemoSongs-MIT.txt) is kept verbatim. The mapping is fairly old and cannot be taken as the current complete song set.

The wiki song pages also include removed songs, port-exclusive songs, and images of different versions, so the number of song pages or images cannot be taken directly as the number of unique songs in the current mobile version. Images from different wikis, multiple versions of the same song, and duplicate provenance records with the same SHA-256 are all kept, so the higher-level gallery can compare them by source.

## Image fidelity

The script saves the raw bytes of the HTTP response; it does not re-encode, upscale, crop, color-correct, or remove backgrounds. Every image's actual format and dimensions are verified with Pillow, and its SHA-256, download time, real Content-Type, source page, request URL, final response URL, and wiki metadata are recorded.

Fandom CDN requests use the public `format=original` option to avoid default negotiation to WebP. The direct file URL is requested first; on a CDN backend error, the full revision URL provided by the API is tried next. If both original endpoints fail, the public full-size `format=png` is tried last; the result is explicitly flagged as possibly re-encoded by the CDN, and its checksum/size is still compared item by item against the original upload. The saved file's extension comes from the actual format verified by decoding; the URL extension is not blindly trusted.

`wiki_original_size_matches` and `wiki_original_sha1_matches` indicate whether the downloaded file matches the original-upload metadata published by the wiki. A few CDN files may have the same dimensions but a different checksum/size; these records do not claim to be identical to the uploaded master file. Files of 1024×2048 or 2048×1024 may be atlases or contact sheets; they are kept as is and flagged, and are not cropped apart automatically.

A public wiki upload is not in itself the artist's working master, and native resolution has not been inferred from color counts. Collecting the files in this repository does not change the original attribution or rights ownership of the artists and Rayark.

## Reproduction

Requirements: Python 3.10+, `requests`, and `Pillow`; fetching the optional Japanese illustrator index also uses `beautifulsoup4`. The script can be run in isolated mode:

```sh
python -I scripts/fetch_wikis.py --workers 4
```

To resume from the saved candidate snapshot only (first verifying the SHA-256 of files that already exist, then retrying missing downloads):

```sh
python -I scripts/fetch_wikis.py --resume --workers 4
```

The script allows at most 4 concurrent requests, sets timeouts and limited retries, and saves the manifest after every 25 results. `--metadata-only` only updates the candidate metadata; an existing final image manifest is kept.

## Access limits in this run

The first pass fully read BWIKI's 480 `allimages` entries and 182 song pages; later API refreshes hit EdgeOne HTTP 567, so the continued downloads used the previously saved candidate snapshot. The final inventory does not claim to include every unverified image on that site. In the source manifest (`wikis.json`), `discovery.excluded_large_images` keeps the names and dimensions of large images that could not be clearly mapped.

[The Japanese DEEMO Wiki's illustrator directory](https://wikiwiki.jp/deemo/アーティスト別リスト2) initially returned Cloudflare 403; it was back to 200 on a normal index request after the downloads finished, and the index has been saved. No challenge was bypassed and no login credentials were used. `wikis.json` is authoritative for what actually succeeded or failed.
