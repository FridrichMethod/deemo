"""Compare a fresh wiki discovery snapshot with the committed one and write a review report.

Standard library only; nothing is downloaded or modified:
    python -I scripts/check_sources.py --baseline OLD.json --current NEW.json \
        --manifest data/sources/wikis.json --report report.md --summary summary.json
The report lists wiki candidates that were added, re-uploaded (different SHA-1 or size)
or removed since the baseline, whether each was uploaded after the baseline snapshot,
and whether a local download already exists for it. Only those file changes open a pull
request; candidates whose mapping metadata (song titles, collections, composers and the
like) or, for an unchanged file, the wiki's file metadata changed are counted and listed as
well, since a merge carries those changes to the records. The report is written in English,
followed by the same report in Simplified Chinese inside a collapsed <details> block;
if both together would exceed REPORT_LIMIT characters, each table lists fewer rows.

A current snapshot whose download URL (info.url, which fetch_wikis.py --resume requests)
is not under its wiki's own image path is refused with an error, so the workflow's
publishing job fails instead of opening a pull request for it.

Wiki editors control file titles, song titles and collection names, and the report becomes
the body of a bot pull request and a run summary. Every value taken from a snapshot is
therefore shown as an inline code span (see code()), so it cannot add mentions, issue
references, links, images, HTML or table cells; the only links are to file pages on the
candidate's own wiki.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LABELS = {"fandom": "Fandom", "bwiki": "BWIKI"}
# The report links a candidate's file page only when it is an https URL on that source's wiki.
SOURCE_HOSTS = {"fandom": "deemo.fandom.com", "bwiki": "wiki.biligame.com"}
# Where each wiki serves its image files. fetch_wikis.py --resume downloads every candidate's info.url, and the snapshot
# is produced by a job that installs and runs third-party code, so a proposed snapshot must keep to these. The path
# prefix matters as much as the host: each CDN serves every wiki of its farm. fetch_wikis.py loads this table and
# download_url_allowed() to check every download URL and redirect target, one definition for both.
DOWNLOAD_LOCATIONS = {
    "fandom": ("static.wikia.nocookie.net", "/deemo/images/"),
    "bwiki": ("patchwiki.biligame.com", "/images/deemo/"),
}
# Characters kept as-is in a linked URL; anything else (spaces, < > | ` \ " and non-ASCII) is percent-encoded.
URL_SAFE = ":/?#[]@!$&'()*+,;=%"
# Control characters and line or paragraph separators would end a table row; bidirectional controls could
# reorder the visible text.
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
BIDI_CONTROL = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
# Whitespace and control characters at either end of a decoded path segment, which a server may trim before it reads
# the segment (see reads_as_dot()).
SEGMENT_PADDING = re.compile(r"^[\s\x00-\x1f\x7f-\x9f]+|[\s\x00-\x1f\x7f-\x9f]+$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?")
FINGERPRINT_FIELDS = ("sha1", "size", "width", "height")
# Candidate fields that fetch_wikis.py --resume copies into the record of a verified file. wiki_extmetadata is the
# snapshot's info.extmetadata, which the record keeps under that name (see metadata_value()).
METADATA_FIELDS = (
    "kind",
    "song_titles",
    "collections",
    "collection_aliases",
    "related_pages",
    "composer",
    "composer_source",
    "artist",
    "variant_note",
    "mapping_method",
    "wiki_extmetadata",
)
SMALL_IMAGE_EDGE = 300
ROW_LIMIT = 100
# GitHub rejects pull request bodies over 65,536 characters; leave room for the footer the workflow appends.
REPORT_LIMIT = 60000
ADDED_COLUMNS = ("source", "file", "kind", "dims", "size", "uploaded", "names")
REUPLOADED_COLUMNS = ("source", "file", "dims", "size", "sha1", "uploaded", "local")
REMOVED_COLUMNS = ("source", "file", "local")
METADATA_COLUMNS = ("source", "file", "changes")
# Report strings per language; a ("singular", "plural") pair is picked by count (see plural()). The values are strings,
# pairs of strings and small lookup tables, so each language maps to Any rather than to the union mypy would infer.
TEXT: dict[str, dict[str, Any]] = {
    "en": {
        "heading": "## Wiki source check",
        "snapshots": "Baseline snapshot {baseline_at} ({baseline}) → current discovery {current_at} ({current}).",
        "candidates": ("{} candidate", "{} candidates"),
        "counts": (
            "- {added} added ({added_after_baseline} of them uploaded after the baseline) · "
            "{reuploaded} re-uploaded · {removed} removed · {metadata_changed} with changed metadata"
        ),
        "unchanged": "The candidate set has not changed; nothing to do.",
        "unchanged_files": (
            "No file was added, re-uploaded or removed, so no pull request is opened. {} candidate has changed "
            "metadata (song titles, collections, composers, other mapping fields or file metadata); the next snapshot "
            "pull request carries the change.",
            "No file was added, re-uploaded or removed, so no pull request is opened. {} candidates have changed "
            "metadata (song titles, collections, composers, other mapping fields or file metadata); the next snapshot "
            "pull request carries the changes.",
        ),
        "section": "### {} ({})",
        "added": "Added",
        "reuploaded": "Re-uploaded",
        "removed": "Removed",
        "metadata": "Changed metadata",
        "columns": {
            "source": "Source",
            "file": "File",
            "kind": "Type",
            "dims": "Dimensions",
            "size": "Size",
            "uploaded": "Uploaded",
            "names": "Song title / collection",
            "sha1": "Wiki SHA-1",
            "local": "Local file",
            "changes": "Changes (before → after)",
        },
        "more": (
            "{} more entry is not listed; see the diff of `data/sources/wiki-discovery.json`.",
            "{} more entries are not listed; see the diff of `data/sources/wiki-discovery.json`.",
        ),
        "small": " ⚠ small image",
        "after_baseline": "{} (after baseline)",
        "no_local": "none",
        "sha1_verdict": {True: "SHA-1 matched at download", False: "SHA-1 did not match at download", None: ""},
        "verdict": " ({})",
        "next_steps": """### Local steps after merging

Merging this PR only updates the discovery snapshot; it includes no images. Afterwards, run locally:

```sh
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
for test in tests/test_*.py; do .venv/bin/python -I "$test" || echo "FAILED: $test"; done
```

`--resume` verifies the SHA-256 and Wiki SHA-1 of existing files against the snapshot and downloads only added and re-uploaded files; the records of the files it verifies take the snapshot's song titles, collections, composers, other mapping fields and file metadata (the changes are listed under "Changed metadata"). `--resume` never deletes files and keeps every previously verified record, marked with `upstream_status`: a re-uploaded file is saved under a new file name and keeps the asset id, while the previous version keeps its file and record as `"upstream_status": "superseded"`; a removed file keeps its record as `"upstream_status": "removed"`; a failed re-download keeps the old record as `"upstream_status": "fetch_failed"`. `build_thumbnails.py --prune` then writes the grid previews of new and re-uploaded images and deletes the previews whose original left the gallery; it runs before the catalog build, which records the previews, and `tests/test_thumbnails.py` fails without them. "Added" entries uploaded before the baseline snapshot are usually changes in the wiki enumeration or the song-title mapping, not newly published artwork. Check the `failures`, `upstream_status` and checksum comparison fields in `data/sources/wikis.json`, then commit the images, the previews (`assets/thumbs/`, `data/thumbs.json`), the manifests (`data/sources/`) and the rebuilt catalog (`data/catalog.json`, `data/catalog.js`, `index.html`).
""",
    },
    "zh-CN": {
        "heading": "## Wiki 来源检查",
        "snapshots": "基线快照 {baseline_at}（{baseline}）→ 本次发现 {current_at}（{current}）。",
        "candidates": "{} 个候选",
        "counts": (
            "- 新增 {added}（其中 {added_after_baseline} 个在基线之后上传）· 重新上传 {reuploaded} · 移除 {removed}"
            " · 元数据变化 {metadata_changed}"
        ),
        "unchanged": "候选集合没有变化，无需处理。",
        "unchanged_files": (
            "没有新增、重新上传或移除的文件，因此不会创建 PR。{} 个候选的元数据（曲名、曲包、作曲者等映射字段或文件元数据）"
            "有变化，下一个快照 PR 会带上这些变化。"
        ),
        "section": "### {}（{}）",
        "added": "新增",
        "reuploaded": "重新上传",
        "removed": "移除",
        "metadata": "元数据变化",
        "columns": {
            "source": "来源",
            "file": "文件",
            "kind": "类型",
            "dims": "尺寸",
            "size": "大小",
            "uploaded": "上传时间",
            "names": "曲名 / 曲包",
            "sha1": "Wiki SHA-1",
            "local": "本地文件",
            "changes": "变化（之前 → 之后）",
        },
        "more": "另有 {} 项未列出，见 `data/sources/wiki-discovery.json` 的 diff。",
        "small": " ⚠小图",
        "after_baseline": "{}（基线之后）",
        "no_local": "无",
        "sha1_verdict": {True: "SHA-1 曾一致", False: "SHA-1 曾不一致", None: ""},
        "verdict": "（{}）",
        "next_steps": """### 合并后的本地步骤

合并此 PR 只更新发现快照，不包含任何图片。随后在本地执行：

```sh
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python scripts/build_catalog.py --verify
for test in tests/test_*.py; do .venv/bin/python -I "$test" || echo "FAILED: $test"; done
```

`--resume` 按快照校验已有文件的 SHA-256 与 Wiki SHA-1，只下载新增和重新上传的文件；校验通过的文件，其记录采用快照中的曲名、曲包、作曲者等映射字段与文件元数据（变化列在"元数据变化"下）。`--resume` 不会删除任何文件，并保留所有已校验的记录，以 `upstream_status` 标记：重新上传的文件以新文件名保存并沿用原资产 ID，旧版本保留文件和记录，标记为 `"upstream_status": "superseded"`；已移除的文件保留记录，标记为 `"upstream_status": "removed"`；重新下载失败时保留旧记录，标记为 `"upstream_status": "fetch_failed"`。随后 `build_thumbnails.py --prune` 为新增和重新上传的图片生成网格预览图，并删除原图已不在图库中的预览图；它须在构建目录之前运行（目录会记录预览图），缺少预览图时 `tests/test_thumbnails.py` 会失败。上传时间早于基线快照的"新增"条目通常是 Wiki 枚举或曲名映射的变化，而不是新发布的曲绘。检查 `data/sources/wikis.json` 中的 `failures`、`upstream_status` 与 checksum 比较字段，然后提交图片、预览图（`assets/thumbs/`、`data/thumbs.json`）、清单（`data/sources/`）与重新生成的目录（`data/catalog.json`、`data/catalog.js`、`index.html`）。
""",
    },
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def asset_id(source: str, file_title: str) -> str:
    """Mirror the asset id derivation in scripts/fetch_wikis.py."""
    return "wikis:" + source + ":" + hashlib.sha256(file_title.encode("utf-8")).hexdigest()[:16]


def index_candidates(snapshot: dict) -> dict[tuple[str, str], dict]:
    candidates: dict[tuple[str, str], dict] = {}
    for candidate in snapshot.get("candidates", []):
        key = (candidate["source"], candidate["file_title"])
        if key in candidates:
            raise ValueError(f"Duplicate candidate in snapshot: {key}")
        candidates[key] = candidate
    return candidates


def reads_as_dot(piece: str) -> bool:
    """Whether a server could read one piece of a URL path (split at "/" and "\\") as "." or "..": without a ";" path
    parameter ("..;x=1"), cut at a NUL (a server written in C stops there), or with the whitespace and control
    characters at either end trimmed ("..%20", "..%0d%0a")."""
    name = piece.split(";", 1)[0].split("\x00", 1)[0]
    return SEGMENT_PADDING.sub("", name) in (".", "..")


def has_dot_segment(path: str) -> bool:
    """Whether a server could read a segment of the URL path as "." or "..", and so leave the directory it names.

    The path is checked as written and after each round of percent-decoding, repeated until nothing changes (a server
    that decodes twice reads "%252e" as "."). In every form a backslash counts as "/", and each piece is read as
    reads_as_dot() says. A round whose bytes are not UTF-8 counts too: a lenient decoder reads the overlong "%c0%ae" as
    "." (MediaWiki titles cannot hold "%" and two hex digits, so a real file name never decodes to such bytes). The
    characters themselves are allowed: MediaWiki file names may hold ";", "\\", "%", spaces and dots.
    """
    while True:
        if any(reads_as_dot(piece) for piece in re.split(r"[/\\]", path)):
            return True
        try:
            decoded = unquote(path, errors="strict")
        except UnicodeDecodeError:
            return True
        # Each round that changes the path shortens it (three characters "%XX" become one), so the loop ends.
        if decoded == path:
            return False
        path = decoded


def download_url_allowed(source: str, url: object) -> bool:
    """Whether url is an https URL under the image path of the source's own wiki: the exact host, with no user, port,
    whitespace, backslash or control character, and no path segment a server could read as "." or ".." (see
    has_dot_segment())."""
    location = DOWNLOAD_LOCATIONS.get(source)
    if location is None or not isinstance(url, str) or CONTROL.search(url) or re.search(r"[\s\\]", url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host, prefix = location
    return (
        parts.scheme == "https"
        and parts.netloc == host
        and parts.path.startswith(prefix)
        and not has_dot_segment(parts.path)
    )


def check_download_urls(snapshot: dict) -> None:
    """Raise ValueError if a candidate's download URL is not under its wiki's image path (see DOWNLOAD_LOCATIONS)."""
    refused = [
        f"{candidate.get('source')!r} {candidate.get('file_title')!r}: {candidate['info']['url']!r}"
        for candidate in snapshot.get("candidates", [])
        if "url" in candidate.get("info", {})
        and not download_url_allowed(candidate.get("source"), candidate["info"]["url"])
    ]
    if refused:
        # repr() keeps the snapshot's text on one line, so it cannot forge a workflow command in the job log.
        raise ValueError(
            "Download URL not under its wiki's image path (DOWNLOAD_LOCATIONS in scripts/check_sources.py): "
            + "; ".join(refused)
        )


def fingerprint(candidate: dict) -> tuple:
    info = candidate.get("info", {})
    return tuple(info.get(field) for field in FINGERPRINT_FIELDS)


def describe(candidate: dict, previous: dict | None, assets: dict[str, dict], baseline_time: str) -> dict:
    info = candidate.get("info", {})
    asset = assets.get(asset_id(candidate["source"], candidate["file_title"]))
    timestamp = info.get("timestamp")
    row = {
        "source": candidate["source"],
        "file_title": candidate["file_title"],
        "kind": candidate.get("kind"),
        "width": info.get("width"),
        "height": info.get("height"),
        "size": info.get("size"),
        "sha1": info.get("sha1"),
        "timestamp": timestamp,
        "uploaded_after_baseline": bool(timestamp and baseline_time and timestamp > baseline_time),
        "page_url": info.get("descriptionurl"),
        "song_titles": list(candidate.get("song_titles", [])),
        "collections": list(candidate.get("collections", [])),
        "local_path": asset["path"] if asset else None,
        "local_sha1_matched_wiki": asset.get("wiki_original_sha1_matches") if asset else None,
    }
    if previous is not None:
        old = previous.get("info", {})
        row["previous"] = {field: old.get(field) for field in FINGERPRINT_FIELDS + ("timestamp",)}
    return row


def metadata_value(candidate: dict, field: str) -> object:
    """A candidate's value of one of METADATA_FIELDS, as the record of a verified file holds it."""
    if field == "wiki_extmetadata":
        # Empty file metadata is left out of a record, like missing metadata.
        return candidate.get("info", {}).get("extmetadata") or None
    return candidate.get(field)


def metadata_changes(candidate: dict, previous: dict) -> list[dict]:
    """The METADATA_FIELDS whose value differs between the previous and the current candidate, in that order.

    wiki_extmetadata counts only while the file is unchanged: a re-uploaded file brings new file metadata anyway (its
    DateTime is the upload time), and the report lists it under Re-uploaded."""
    reuploaded = fingerprint(candidate) != fingerprint(previous)
    return [
        {"field": field, "old": metadata_value(previous, field), "new": metadata_value(candidate, field)}
        for field in METADATA_FIELDS
        if metadata_value(candidate, field) != metadata_value(previous, field)
        and not (field == "wiki_extmetadata" and reuploaded)
    ]


def compare(baseline: dict, current: dict, manifest: dict) -> dict:
    check_download_urls(current)
    old = index_candidates(baseline)
    new = index_candidates(current)
    assets = {asset["id"]: asset for asset in manifest.get("assets", [])}
    baseline_time = str(baseline.get("fetched_at") or "")
    added = [describe(new[key], None, assets, baseline_time) for key in sorted(new) if key not in old]
    removed = [describe(old[key], None, assets, baseline_time) for key in sorted(old) if key not in new]
    reuploaded = [
        describe(new[key], old[key], assets, baseline_time)
        for key in sorted(new)
        if key in old and fingerprint(new[key]) != fingerprint(old[key])
    ]
    # Mapping changes open no pull request on their own (the candidate set is the files), but a merge carries them.
    metadata_changed = [
        {**describe(new[key], None, assets, baseline_time), "changes": metadata_changes(new[key], old[key])}
        for key in sorted(new)
        if key in old and metadata_changes(new[key], old[key])
    ]
    counts = {
        "added": len(added),
        "reuploaded": len(reuploaded),
        "removed": len(removed),
        "metadata_changed": len(metadata_changed),
        "added_after_baseline": sum(row["uploaded_after_baseline"] for row in added),
        "baseline": len(old),
        "current": len(new),
    }
    date = str(current.get("fetched_at") or "")[:10]
    date = date if DATE.fullmatch(date) else "unknown-date"
    return {
        "changed": bool(added or removed or reuploaded),
        "title": (
            f"Wiki source update: {counts['added']} added · {counts['reuploaded']} re-uploaded · "
            f"{counts['removed']} removed ({date})"
        ),
        "counts": counts,
        "baseline_fetched_at": baseline.get("fetched_at"),
        "current_fetched_at": current.get("fetched_at"),
        "added": added,
        "reuploaded": reuploaded,
        "removed": removed,
        "metadata_changed": metadata_changed,
    }


def code(value) -> str:
    """Show untrusted text as one inline code span that stays inside its table cell.

    GitHub renders nothing inside a code span: no mentions, references, links, images or HTML.
    The fence is one backtick longer than any backtick run in the text, and a pipe is escaped
    as \\| because GitHub splits table cells before it parses code spans.
    """
    text = BIDI_CONTROL.sub("", CONTROL.sub(" ", str(value))).replace("|", "\\|")
    if not text.strip():
        return "?"
    fence = "`" * (max(map(len, re.findall("`+", text)), default=0) + 1)
    pad = " " if text[0] == "`" or text[-1] == "`" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def timestamp(value) -> str:
    if not value:
        return "?"
    return value if isinstance(value, str) and TIMESTAMP.fullmatch(value) else code(value)


def plural(forms, count: int) -> str:
    """Fill in count; forms is one string, or a ("singular", "plural") pair chosen by count."""
    return (forms if isinstance(forms, str) else forms[count != 1]).format(count)


def short_title(file_title: str) -> str:
    return file_title.split(":", 1)[-1]


def page_url(row: dict) -> str | None:
    """The file page URL, percent-encoded for a Markdown link, if it is https on the row's own wiki."""
    url = row.get("page_url")
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        trusted = (
            parts.scheme == "https"
            and parts.hostname == SOURCE_HOSTS.get(row["source"])
            and parts.port is None
            and not parts.username
            and not parts.password
        )
    except ValueError:
        return None
    return quote(url, safe=URL_SAFE) if trusted else None


def link(row: dict) -> str:
    text, url = code(short_title(str(row["file_title"]))), page_url(row)
    return f"[{text}](<{url}>)" if url else text


def source(row: dict) -> str:
    return SOURCE_LABELS.get(row["source"]) or code(row["source"])


def dims(width, height, lang: str = "en") -> str:
    if not all(isinstance(edge, int) and edge > 0 for edge in (width, height)):
        return "?"
    flag = TEXT[lang]["small"] if min(width, height) < SMALL_IMAGE_EDGE else ""
    return f"{width}×{height}{flag}"


def mib(size) -> str:
    return f"{size / 1048576:.2f} MiB" if isinstance(size, (int, float)) else "?"


def short_sha(sha1) -> str:
    return code(str(sha1)[:10] if sha1 else "?")


def uploaded(row: dict, lang: str = "en") -> str:
    stamp = str(row.get("timestamp") or "")[:10]
    if not DATE.fullmatch(stamp):
        return "?"
    return TEXT[lang]["after_baseline"].format(stamp) if row.get("uploaded_after_baseline") else stamp


def local(row: dict, lang: str = "en") -> str:
    text = TEXT[lang]
    if not row.get("local_path"):
        return text["no_local"]
    verdict = text["sha1_verdict"][row.get("local_sha1_matched_wiki")]
    return code(row["local_path"]) + (text["verdict"].format(verdict) if verdict else "")


def added_row(row: dict, lang: str = "en") -> list[str]:
    names = " / ".join(code(name) for name in row["song_titles"] + row["collections"]) or "—"
    return [
        source(row),
        link(row),
        code(row["kind"]) if row["kind"] else "?",
        dims(row["width"], row["height"], lang),
        mib(row["size"]),
        uploaded(row, lang),
        names,
    ]


def reuploaded_row(row: dict, lang: str = "en") -> list[str]:
    old = row["previous"]
    return [
        source(row),
        link(row),
        f"{dims(old['width'], old['height'], lang)} → {dims(row['width'], row['height'], lang)}",
        f"{mib(old['size'])} → {mib(row['size'])}",
        f"{short_sha(old['sha1'])} → {short_sha(row['sha1'])}",
        uploaded(row, lang),
        local(row, lang),
    ]


def removed_row(row: dict, lang: str = "en") -> list[str]:
    return [source(row), link(row), local(row, lang)]


def field_value(value) -> str:
    """A metadata value as code spans: a list joined with " / ", and a missing or empty value as a dash."""
    if value is None or value == [] or value == "":
        return "—"
    return " / ".join(code(item) for item in value) if isinstance(value, list) else code(value)


def metadata_row(row: dict, lang: str = "en") -> list[str]:
    changes = "; ".join(
        f"{code(change['field'])}: {field_value(change['old'])} → {field_value(change['new'])}"
        for change in row["changes"]
    )
    return [source(row), link(row), changes]


def table(section: str, rows: list[dict], columns: tuple[str, ...], render_row, lang: str, limit: int) -> list[str]:
    if not rows:
        return []
    text = TEXT[lang]
    headers = [text["columns"][column] for column in columns]
    lines = [
        text["section"].format(text[section], len(rows)),
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines += ["| " + " | ".join(render_row(row, lang)) + " |" for row in rows[:limit]]
    if len(rows) > limit:
        lines.append("\n" + plural(text["more"], len(rows) - limit))
    return lines + [""]


def render(report: dict, lang: str = "en", limit: int | None = None) -> str:
    """Render the report in one language ("en" or "zh-CN"), listing at most limit (default ROW_LIMIT) rows per table."""
    text, counts = TEXT[lang], report["counts"]
    limit = ROW_LIMIT if limit is None else limit
    lines = [
        text["heading"],
        "",
        text["snapshots"].format(
            baseline_at=timestamp(report["baseline_fetched_at"]),
            baseline=plural(text["candidates"], counts["baseline"]),
            current_at=timestamp(report["current_fetched_at"]),
            current=plural(text["candidates"], counts["current"]),
        ),
        "",
        text["counts"].format(**counts),
        "",
    ]
    metadata = table("metadata", report["metadata_changed"], METADATA_COLUMNS, metadata_row, lang, limit)
    if not report["changed"]:
        if not report["metadata_changed"]:
            return "\n".join(lines + [text["unchanged"], ""])
        return "\n".join(lines + [plural(text["unchanged_files"], counts["metadata_changed"]), ""] + metadata)
    lines += table("added", report["added"], ADDED_COLUMNS, added_row, lang, limit)
    lines += table("reuploaded", report["reuploaded"], REUPLOADED_COLUMNS, reuploaded_row, lang, limit)
    lines += table("removed", report["removed"], REMOVED_COLUMNS, removed_row, lang, limit)
    return "\n".join(lines + metadata + [text["next_steps"]])


def render_bilingual(report: dict) -> str:
    """The English report, then the same report in Simplified Chinese in a collapsed <details> block.

    Above REPORT_LIMIT characters, both languages are rendered again with fewer rows per table until they fit.
    """
    limit = ROW_LIMIT
    while True:
        markdown = (
            f"{render(report, 'en', limit)}\n<details>\n<summary>简体中文</summary>\n\n"
            f"{render(report, 'zh-CN', limit)}\n</details>\n"
        )
        if len(markdown) <= REPORT_LIMIT or limit <= 0:
            return markdown
        limit = min(limit - 1, limit * REPORT_LIMIT // len(markdown))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True, type=Path, help="Committed wiki-discovery.json")
    parser.add_argument("--current", required=True, type=Path, help="Freshly discovered wiki-discovery.json")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data/sources/wikis.json",
        help="wikis.json manifest used to locate existing downloads",
    )
    parser.add_argument("--report", type=Path, help="Write the Markdown report to this path")
    parser.add_argument("--summary", type=Path, help="Write the JSON comparison to this path")
    args = parser.parse_args()
    manifest = load(args.manifest) if args.manifest.is_file() else {}
    report = compare(load(args.baseline), load(args.current), manifest)
    if args.report:
        args.report.write_text(render_bilingual(report), encoding="utf-8")
    if args.summary:
        args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"changed": report["changed"], "title": report["title"], "counts": report["counts"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
