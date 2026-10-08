"""Compare a fresh wiki discovery snapshot with the committed one and write a review report.

Standard library only; nothing is downloaded or modified:
    python -I scripts/check_sources.py --baseline OLD.json --current NEW.json \
        --manifest data/sources/wikis.json --report report.md --summary summary.json
The report lists wiki candidates that were added, re-uploaded (different SHA-1 or size)
or removed since the baseline, whether each was uploaded after the baseline snapshot,
and whether a local download already exists for it.
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
NEXT_STEPS = """### 合并后的本地步骤

合并此 PR 只更新发现快照，不包含任何图片。随后在本地执行：

```sh
.venv/bin/python -I scripts/fetch_wikis.py --resume --workers 4
.venv/bin/python scripts/build_catalog.py --verify
.venv/bin/python -I tests/test_catalog.py
.venv/bin/python -I tests/test_layout.py
```

`--resume` 按快照校验已有文件的 SHA-256 与 Wiki SHA-1，只下载新增和重新上传的文件；重新上传的文件会以新文件名保存，旧文件不会自动删除。上传时间早于基线快照的"新增"条目通常是 Wiki 枚举或曲名映射的变化，而不是新发布的曲绘。检查 `data/sources/wikis.json` 中的 `failures` 与 checksum 比较字段，再提交图片与清单。
"""


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
        "title": f"Wiki 来源更新：新增 {counts['added']} · 重新上传 {counts['reuploaded']} · 移除 {counts['removed']}（{date}）",
        "counts": counts,
        "baseline_fetched_at": baseline.get("fetched_at"),
        "current_fetched_at": current.get("fetched_at"),
        "added": added,
        "reuploaded": reuploaded,
        "removed": removed,
    }


def escape(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def short_title(file_title: str) -> str:
    return file_title.split(":", 1)[-1]


def link(row: dict) -> str:
    text = escape(short_title(row["file_title"]))
    return f"[{text}]({escape(row['page_url'])})" if row.get("page_url") else text


def dims(width, height) -> str:
    if not (width and height):
        return "?"
    flag = " ⚠小图" if min(width, height) < SMALL_IMAGE_EDGE else ""
    return f"{width}×{height}{flag}"


def mib(size) -> str:
    return f"{size / 1048576:.2f} MiB" if isinstance(size, (int, float)) else "?"


def short_sha(sha1) -> str:
    return str(sha1)[:10] if sha1 else "?"


def uploaded(row: dict) -> str:
    stamp = str(row.get("timestamp") or "?")[:10]
    return f"{stamp}（基线之后）" if row.get("uploaded_after_baseline") else stamp


def local(row: dict) -> str:
    if not row.get("local_path"):
        return "无"
    verdict = {True: "SHA-1 曾一致", False: "SHA-1 曾不一致", None: ""}[row.get("local_sha1_matched_wiki")]
    return f"`{escape(row['local_path'])}`" + (f"（{verdict}）" if verdict else "")


def added_row(row: dict) -> list[str]:
    names = " / ".join(row["song_titles"] + row["collections"]) or "—"
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row), escape(row["kind"] or "?"),
            dims(row["width"], row["height"]), mib(row["size"]), uploaded(row), escape(names)]


def reuploaded_row(row: dict) -> list[str]:
    old = row["previous"]
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row),
            f"{dims(old['width'], old['height'])} → {dims(row['width'], row['height'])}",
            f"{mib(old['size'])} → {mib(row['size'])}",
            f"`{short_sha(old['sha1'])}` → `{short_sha(row['sha1'])}`", uploaded(row), local(row)]


def removed_row(row: dict) -> list[str]:
    return [SOURCE_LABELS.get(row["source"], row["source"]), link(row), local(row)]


def table(heading: str, rows: list[dict], columns: list[str], render_row) -> list[str]:
    if not rows:
        return []
    lines = [f"### {heading}（{len(rows)}）", "", "| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(render_row(row)) + " |" for row in rows[:ROW_LIMIT]]
    if len(rows) > ROW_LIMIT:
        lines.append(f"\n另有 {len(rows) - ROW_LIMIT} 项未列出，见 `data/sources/wiki-discovery.json` 的 diff。")
    return lines + [""]


def render(report: dict) -> str:
    counts = report["counts"]
    lines = [
        "## Wiki 来源检查", "",
        (f"基线快照 {report['baseline_fetched_at'] or '?'}（{counts['baseline']} 个候选）→ "
         f"本次发现 {report['current_fetched_at'] or '?'}（{counts['current']} 个候选）。"), "",
        (f"- 新增 {counts['added']}（其中 {counts['added_after_baseline']} 个在基线之后上传）· "
         f"重新上传 {counts['reuploaded']} · 移除 {counts['removed']}"), "",
    ]
    if not report["changed"]:
        return "\n".join(lines + ["候选集合没有变化，无需处理。", ""])
    lines += table("新增", report["added"], ["来源", "文件", "类型", "尺寸", "大小", "上传时间", "曲名 / 曲包"], added_row)
    lines += table("重新上传", report["reuploaded"],
                   ["来源", "文件", "尺寸", "大小", "Wiki SHA-1", "上传时间", "本地文件"], reuploaded_row)
    lines += table("移除", report["removed"], ["来源", "文件", "本地文件"], removed_row)
    return "\n".join(lines + [NEXT_STEPS])


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
        args.report.write_text(render(report), encoding="utf-8")
    if args.summary:
        args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed": report["changed"], "title": report["title"], "counts": report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
