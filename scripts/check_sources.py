"""Compare a fresh wiki discovery snapshot with the committed one and write a review report.

Standard library only; nothing is downloaded or modified:
    python -I scripts/check_sources.py --baseline OLD.json --current NEW.json \
        --manifest data/sources/wikis.json --report report.md --summary summary.json
The report lists wiki candidates that were added, re-uploaded (different SHA-1 or size)
or removed since the baseline, whether each was uploaded after the baseline snapshot,
and whether a local download already exists for it. The report is written in English,
followed by the same report in Simplified Chinese inside a collapsed <details> block;
if both together would exceed REPORT_LIMIT characters, each table lists fewer rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LABELS = {"fandom": "Fandom", "bwiki": "BWIKI"}
FINGERPRINT_FIELDS = ("sha1", "size", "width", "height")
SMALL_IMAGE_EDGE = 300
ROW_LIMIT = 100
# GitHub rejects pull request bodies over 65,536 characters; leave room for the footer the workflow appends.
REPORT_LIMIT = 60000
ADDED_COLUMNS = ("source", "file", "kind", "dims", "size", "uploaded", "names")
REUPLOADED_COLUMNS = ("source", "file", "dims", "size", "sha1", "uploaded", "local")
REMOVED_COLUMNS = ("source", "file", "local")
# Report strings per language; a ("singular", "plural") pair is picked by count (see plural()).
TEXT = {
    "en": {
        "heading": "## Wiki source check",
        "snapshots": "Baseline snapshot {baseline_at} ({baseline}) → current discovery {current_at} ({current}).",
        "candidates": ("{} candidate", "{} candidates"),
        "counts": ("- {added} added ({added_after_baseline} of them uploaded after the baseline) · "
                   "{reuploaded} re-uploaded · {removed} removed"),
        "unchanged": "The candidate set has not changed; nothing to do.",
        "section": "### {} ({})",
        "added": "Added", "reuploaded": "Re-uploaded", "removed": "Removed",
        "columns": {"source": "Source", "file": "File", "kind": "Type", "dims": "Dimensions", "size": "Size",
                    "uploaded": "Uploaded", "names": "Song title / collection", "sha1": "Wiki SHA-1",
                    "local": "Local file"},
        "more": ("{} more entry is not listed; see the diff of `data/sources/wiki-discovery.json`.",
                 "{} more entries are not listed; see the diff of `data/sources/wiki-discovery.json`."),
        "small": " ⚠ small image",
        "after_baseline": "{} (after baseline)",
        "no_local": "none",
        "sha1_verdict": {True: "SHA-1 matched at download", False: "SHA-1 did not match at download", None: ""},
        "verdict": " ({})",
        "next_steps": """### Local steps after merging

Merging this PR only updates the discovery snapshot; it includes no images. Afterwards, run locally:

```sh
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I tests/test_catalog.py
.venv/bin/python -I tests/test_layout.py
```

`--resume` verifies the SHA-256 and Wiki SHA-1 of existing files against the snapshot and downloads only added and re-uploaded files; re-uploaded files are saved under a new file name, and old files are not deleted automatically. "Added" entries uploaded before the baseline snapshot are usually changes in the wiki enumeration or the song-title mapping, not newly published artwork. Check the `failures` and checksum comparison fields in `data/sources/wikis.json` before committing the images and the manifest.
""",
    },
    "zh-CN": {
        "heading": "## Wiki 来源检查",
        "snapshots": "基线快照 {baseline_at}（{baseline}）→ 本次发现 {current_at}（{current}）。",
        "candidates": "{} 个候选",
        "counts": "- 新增 {added}（其中 {added_after_baseline} 个在基线之后上传）· 重新上传 {reuploaded} · 移除 {removed}",
        "unchanged": "候选集合没有变化，无需处理。",
        "section": "### {}（{}）",
        "added": "新增", "reuploaded": "重新上传", "removed": "移除",
        "columns": {"source": "来源", "file": "文件", "kind": "类型", "dims": "尺寸", "size": "大小",
                    "uploaded": "上传时间", "names": "曲名 / 曲包", "sha1": "Wiki SHA-1", "local": "本地文件"},
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
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I tests/test_catalog.py
.venv/bin/python -I tests/test_layout.py
```

`--resume` 按快照校验已有文件的 SHA-256 与 Wiki SHA-1，只下载新增和重新上传的文件；重新上传的文件会以新文件名保存，旧文件不会自动删除。上传时间早于基线快照的"新增"条目通常是 Wiki 枚举或曲名映射的变化，而不是新发布的曲绘。检查 `data/sources/wikis.json` 中的 `failures` 与 checksum 比较字段，再提交图片与清单。
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


def compare(baseline: dict, current: dict, manifest: dict) -> dict:
    old = index_candidates(baseline)
    new = index_candidates(current)
    assets = {asset["id"]: asset for asset in manifest.get("assets", [])}
    baseline_time = str(baseline.get("fetched_at") or "")
    added = [describe(new[key], None, assets, baseline_time) for key in sorted(new) if key not in old]
    removed = [describe(old[key], None, assets, baseline_time) for key in sorted(old) if key not in new]
    reuploaded = [describe(new[key], old[key], assets, baseline_time) for key in sorted(new)
                  if key in old and fingerprint(new[key]) != fingerprint(old[key])]
    counts = {"added": len(added), "reuploaded": len(reuploaded), "removed": len(removed),
              "added_after_baseline": sum(row["uploaded_after_baseline"] for row in added),
              "baseline": len(old), "current": len(new)}
    date = str(current.get("fetched_at") or "")[:10] or "unknown-date"
    return {
        "changed": bool(added or removed or reuploaded),
        "title": (f"Wiki source update: {counts['added']} added · {counts['reuploaded']} re-uploaded · "
                  f"{counts['removed']} removed ({date})"),
        "counts": counts,
        "baseline_fetched_at": baseline.get("fetched_at"),
        "current_fetched_at": current.get("fetched_at"),
        "added": added,
        "reuploaded": reuploaded,
        "removed": removed,
    }


def escape(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def plural(forms, count: int) -> str:
    """Fill in count; forms is one string, or a ("singular", "plural") pair chosen by count."""
    return (forms if isinstance(forms, str) else forms[count != 1]).format(count)


def short_title(file_title: str) -> str:
    return file_title.split(":", 1)[-1]


def link(row: dict) -> str:
    text = escape(short_title(row["file_title"]))
    return f"[{text}]({escape(row['page_url'])})" if row.get("page_url") else text


def dims(width, height, lang: str = "en") -> str:
    if not (width and height):
        return "?"
    flag = TEXT[lang]["small"] if min(width, height) < SMALL_IMAGE_EDGE else ""
    return f"{width}×{height}{flag}"


def mib(size) -> str:
    return f"{size / 1048576:.2f} MiB" if isinstance(size, (int, float)) else "?"


def short_sha(sha1) -> str:
    return str(sha1)[:10] if sha1 else "?"


def uploaded(row: dict, lang: str = "en") -> str:
    stamp = str(row.get("timestamp") or "?")[:10]
    return TEXT[lang]["after_baseline"].format(stamp) if row.get("uploaded_after_baseline") else stamp


def local(row: dict, lang: str = "en") -> str:
    text = TEXT[lang]
    if not row.get("local_path"):
        return text["no_local"]
    verdict = text["sha1_verdict"][row.get("local_sha1_matched_wiki")]
    return f"`{escape(row['local_path'])}`" + (text["verdict"].format(verdict) if verdict else "")


def added_row(row: dict, lang: str = "en") -> list[str]:
    names = " / ".join(row["song_titles"] + row["collections"]) or "—"
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row), escape(row["kind"] or "?"),
            dims(row["width"], row["height"], lang), mib(row["size"]), uploaded(row, lang), escape(names)]


def reuploaded_row(row: dict, lang: str = "en") -> list[str]:
    old = row["previous"]
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row),
            f"{dims(old['width'], old['height'], lang)} → {dims(row['width'], row['height'], lang)}",
            f"{mib(old['size'])} → {mib(row['size'])}",
            f"`{short_sha(old['sha1'])}` → `{short_sha(row['sha1'])}`", uploaded(row, lang), local(row, lang)]


def removed_row(row: dict, lang: str = "en") -> list[str]:
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row), local(row, lang)]


def table(section: str, rows: list[dict], columns: tuple[str, ...], render_row, lang: str, limit: int) -> list[str]:
    if not rows:
        return []
    text = TEXT[lang]
    headers = [text["columns"][column] for column in columns]
    lines = [text["section"].format(text[section], len(rows)), "", "| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(render_row(row, lang)) + " |" for row in rows[:limit]]
    if len(rows) > limit:
        lines.append("\n" + plural(text["more"], len(rows) - limit))
    return lines + [""]


def render(report: dict, lang: str = "en", limit: int | None = None) -> str:
    """Render the report in one language ("en" or "zh-CN"), listing at most limit (default ROW_LIMIT) rows per table."""
    text, counts = TEXT[lang], report["counts"]
    limit = ROW_LIMIT if limit is None else limit
    lines = [
        text["heading"], "",
        text["snapshots"].format(baseline_at=report["baseline_fetched_at"] or "?",
                                 baseline=plural(text["candidates"], counts["baseline"]),
                                 current_at=report["current_fetched_at"] or "?",
                                 current=plural(text["candidates"], counts["current"])), "",
        text["counts"].format(**counts), "",
    ]
    if not report["changed"]:
        return "\n".join(lines + [text["unchanged"], ""])
    lines += table("added", report["added"], ADDED_COLUMNS, added_row, lang, limit)
    lines += table("reuploaded", report["reuploaded"], REUPLOADED_COLUMNS, reuploaded_row, lang, limit)
    lines += table("removed", report["removed"], REMOVED_COLUMNS, removed_row, lang, limit)
    return "\n".join(lines + [text["next_steps"]])


def render_bilingual(report: dict) -> str:
    """The English report, then the same report in Simplified Chinese in a collapsed <details> block.

    Above REPORT_LIMIT characters, both languages are rendered again with fewer rows per table until they fit.
    """
    limit = ROW_LIMIT
    while True:
        markdown = (f"{render(report, 'en', limit)}\n<details>\n<summary>简体中文</summary>\n\n"
                    f"{render(report, 'zh-CN', limit)}\n</details>\n")
        if len(markdown) <= REPORT_LIMIT or limit <= 0:
            return markdown
        limit = min(limit - 1, limit * REPORT_LIMIT // len(markdown))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True, type=Path, help="Committed wiki-discovery.json")
    parser.add_argument("--current", required=True, type=Path, help="Freshly discovered wiki-discovery.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/sources/wikis.json",
                        help="wikis.json manifest used to locate existing downloads")
    parser.add_argument("--report", type=Path, help="Write the Markdown report to this path")
    parser.add_argument("--summary", type=Path, help="Write the JSON comparison to this path")
    args = parser.parse_args()
    manifest = load(args.manifest) if args.manifest.is_file() else {}
    report = compare(load(args.baseline), load(args.current), manifest)
    if args.report:
        args.report.write_text(render_bilingual(report), encoding="utf-8")
    if args.summary:
        args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed": report["changed"], "title": report["title"], "counts": report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
