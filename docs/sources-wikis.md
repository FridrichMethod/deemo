# DEEMO 1 public wiki artwork sources

**English** · [简体中文](sources-wikis.zh-CN.md)

This directory records the public song artwork from the Fandom DEEMO Wiki and the Bilibili DEEMO Wiki. The downloads cover the original DEEMO and its Last Recital / Reborn ports; DEEMO II, audio, and charts are not included.

Import result on 2026-09-05: **all 757 / 757 candidate images succeeded, with 0 final failures**, totaling **457,462,452 bytes** (about 436.27 MiB). Fandom supplied 561 images and BWIKI 196; of these, 662 are song artwork and 95 are collection covers; the actual formats are 756 PNG and 1 JPEG.

A full audit checked each file's byte count, SHA-256, SHA-1, and actual format and dimensions as read by Pillow, and confirmed that the manifest's unique IDs and the files on disk correspond one to one; all 757 passed, with no orphaned files. 753 match the original-upload SHA-1 published by the wiki. 4 differ: `Spring Snowflake Flower`, `Ark of Desire`, `I Race The Dawn x Sunset`, and `Protest`; all of them keep their mismatch flags. 7 were retrieved through the full-size `format=png` fallback, and their SHA-1 values also all ended up matching the original uploads.

Incremental update on 2026-10-08: the source-check workflow found 57 BWIKI collection cover candidates (all uploaded in 2021; they reflect a difference in the `allimages` enumeration rather than newly published artwork). They were imported with `--resume`, and all of them match the wiki's original-upload SHA-1; in addition, the `collections`/`related_pages` of 11 existing records were updated along with the new candidate metadata. Five of the 57, the 70×47 `* Collections Titletab` images, are UI title tabs rather than covers: BWIKI discovery did not yet apply the UI filter used for Fandom. They were removed from the snapshot, the manifest and the repository on 2026-10-09, and discovery now excludes them. The remaining 52 are thumbnails of about 180×180. The total is now **809 images** (Fandom 561, BWIKI 248; song artwork 662, collection covers 147), **459,809,756 bytes** in all (about 438.51 MiB), 808 PNG and 1 JPEG; the SHA-1 mismatches are still the same 4 images listed above.

Metadata corrections on 2026-10-09, with no image changed: the snapshot (`wiki-discovery.json`) and the manifest (`wikis.json`) were patched to match what the current fetcher produces. The `collections` of 112 records listed one collection under several spellings that differ only in case, spacing or punctuation (`Etude Collection` / `Etude collection`); each now keeps one spelling. 16 BWIKI collection covers now give their collection, and the BWIKI page in `related_pages`, in BWIKI's own spelling (`RAC collection -1` rather than `RAC Collection #1`), or, for the 5 collections that no BWIKI song page names, in the first Fandom spelling in code-point order. The `delivery_note` of the 7 `format=png` downloads no longer says they may be CDN-reencoded, since their SHA-1 and size match the original upload. Two more patches followed, also computed with the fetcher's own functions from the committed snapshot and song index. 290 records gained `collection_aliases`, the other spellings that the song pages of either wiki use for their collections (a record listing `RAC Collection #1` also carries `RAC collection -1`); the archive search matches them, but the viewer does not show them. The BWIKI covers `RAC collection -4`, `-5` and `-6` now give in `related_pages` the BWIKI page named after their file, because a MediaWiki page title cannot contain `#`.

## Files and sources

- `assets/public/wikis/fandom/`: image references are taken from the song pages in the original DEEMO's `Category:Songs` and from the original DEEMO's collection pages.
- `assets/public/wikis/bwiki/`: every page of `allimages` is enumerated and image names are matched against the normalized titles in the song index; collection covers that can be confirmed are included as well. UI images (title tabs, logos, screenshots, DEEMO II) are excluded with the same filter as on Fandom, and collection covers from either wiki need a short edge of at least 100 px.
- `data/sources/wikis.json`: the final image inventory, download status, and failure records; it is the input to the gallery integration. `collections` lists one spelling per collection; `collection_aliases` keeps the other spellings that the song pages use, for search only.
- `data/sources/wiki-discovery.json`: the candidate snapshot, which can be re-checked and used to resume downloading; it contains the raw MediaWiki imageinfo and, under `stats`, the statistics of the enumeration that produced it (song pages, collection pages or `allimages` entries, excluded images). `--resume` copies these statistics into `discovery` in `wikis.json`, together with the snapshot time as `snapshot_fetched_at`.
- `data/sources/wiki-song-index.json`: titles, collections, composers, and image references from the public song pages. The template field `Artist` is the composer; it is stored as `composer` and is not mistaken for the illustrator.
- `data/sources/wiki-illustrator-index.json`: 479 heading/table rows from the Japanese wiki's directory, saved in their original page order. The page contains composer and Vocalist sections as well as a separate `illustrator` section; the last of these lists 9 illustrators, and the other sections are not treated as illustrator attribution.
- `data/sources/song-mapping.json`: legacy in-game internal keys and song information from [syuchan1005/DeemoSongs](https://github.com/syuchan1005/DeemoSongs); its [MIT license](../licenses/DeemoSongs-MIT.txt) is kept verbatim. The mapping is fairly old and cannot be taken as the current complete song set.

The wiki song pages also include removed songs, port-exclusive songs, and images of different versions, so the number of song pages or images cannot be taken directly as the number of unique songs in the current mobile version. Images from different wikis, multiple versions of the same song, and duplicate provenance records with the same SHA-256 are all kept, so the higher-level gallery can compare them by source.

## Image fidelity

The script saves the raw bytes of the HTTP response; it does not re-encode, upscale, crop, color-correct, or remove backgrounds. Every image's actual format and dimensions are verified with Pillow, and its SHA-256, download time, real Content-Type, source page, request URL, final response URL, and wiki metadata are recorded.

Fandom CDN requests use the public `format=original` option to avoid default negotiation to WebP. The direct file URL is requested first; on a CDN backend error, the full revision URL provided by the API is tried next. If both original endpoints fail, the public full-size `format=png` is tried last; its checksum/size is still compared item by item against the original upload, and its `delivery_note` flags it as possibly re-encoded by the CDN only when they differ (otherwise the note says they match). The saved file's extension comes from the actual format verified by decoding; the URL extension is not blindly trusted.

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

`--resume` never drops a record from `wikis.json` and never deletes a file. A record whose candidate is no longer in the snapshot is kept with `"upstream_status": "removed"`. When a re-uploaded file is downloaded, the new version keeps the asset ID, so deep links stay stable, and the previous version keeps its file and record under the ID `<asset ID>:<first 12 hex digits of its SHA-256>`, with `"upstream_status": "superseded"` and `"superseded_by"` naming the current ID. If a re-download fails, the previous record stays, marked `"upstream_status": "fetch_failed"`, next to its entry in `failures`; the next `--resume` retries it.

The script allows at most 4 concurrent requests, sets timeouts and limited retries (at most 3 attempts on connection errors, timeouts, HTTP 429, 500, 502, 503 and 504, and BWIKI's EdgeOne 567, honouring `Retry-After` up to 30 s; any other HTTP error fails at once), and saves the manifest after every 25 results; every save replaces the file atomically and still lists every previous record, so an interrupted run loses nothing. `--metadata-only` downloads no images and rewrites only `wiki-discovery.json` (candidates and statistics) and `wiki-song-index.json`; it keeps an existing `wikis.json` and leaves `song-mapping.json` alone, because the DeemoSongs mapping is fetched only by a full run (without `--resume` or `--metadata-only`).

## Access limits in this run

The first pass on 2026-09-05 read 480 BWIKI `allimages` entries and 182 song pages; later API refreshes hit EdgeOne HTTP 567, so the continued downloads used the previously saved candidate snapshot. That listing was not complete: the source check's enumeration on 2026-10-08 returned 57 more collection-cover candidates, all uploaded in 2021. The 2026-10-08 snapshot does not record its statistics, so until a newer discovery is imported, `discovery` in `wikis.json` (480 entries, 40 `excluded_large_images`) still describes the 2026-09-05 enumeration. The final inventory does not claim to include every unverified image on that site. In the source manifest (`wikis.json`), `discovery.excluded_large_images` keeps the names and dimensions of large images that could not be clearly mapped.

[The Japanese DEEMO Wiki's illustrator directory](https://wikiwiki.jp/deemo/アーティスト別リスト2) initially returned Cloudflare 403; it was back to 200 on a normal index request after the downloads finished, and the index has been saved. No challenge was bypassed and no login credentials were used. `wikis.json` is authoritative for what actually succeeded or failed.
