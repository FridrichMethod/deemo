"""Checks for the wiki discovery comparison used by the scheduled source check."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("check_sources", Path(__file__).resolve().parents[1] / "scripts/check_sources.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

NO_CHANGE = {"added": 0, "reuploaded": 0, "removed": 0, "added_after_baseline": 0}


def candidate(source, name, sha1="a" * 40, size=1000, width=1024, height=1024, timestamp="2026-01-01T00:00:00Z", **extra):
    return {
        "source": source, "file_title": f"File:{name}", "kind": extra.pop("kind", "song_art"),
        "info": {"sha1": sha1, "size": size, "width": width, "height": height, "timestamp": timestamp,
                 "descriptionurl": f"https://example.test/wiki/File:{name}"},
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
        self.assertIn("没有变化", check.render(report))
        self.assertNotIn("### ", check.render(report))

    def test_added_reuploaded_and_removed_candidates(self):
        baseline = snapshot(candidate("fandom", "Kept.png"), candidate("bwiki", "Gone.png"), candidate("fandom", "Same.png"))
        current = snapshot(candidate("fandom", "Kept.png", sha1="b" * 40, size=2000, width=2048, height=2048, timestamp="2026-02-10T00:00:00Z"),
                           candidate("fandom", "Same.png"), candidate("bwiki", "Old.png", width=180, height=180),
                           candidate("bwiki", "New.png", kind="collection_cover", collections=["Pack"], timestamp="2026-02-20T00:00:00Z"))
        report = check.compare(baseline, current, self.manifest)
        self.assertTrue(report["changed"])
        self.assertEqual(report["counts"], {"added": 2, "reuploaded": 1, "removed": 1, "added_after_baseline": 1, "baseline": 3, "current": 4})
        self.assertEqual(report["title"], "Wiki 来源更新：新增 2 · 重新上传 1 · 移除 1（2026-02-02）")
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
        self.assertIn("新增 2（其中 1 个在基线之后上传）", markdown)
        self.assertIn("180×180 ⚠小图", markdown)
        self.assertIn("2026-02-20（基线之后）", markdown)
        self.assertIn("`aaaaaaaaaa` → `bbbbbbbbbb`", markdown)
        self.assertIn("SHA-1 曾一致", markdown)

    def test_bwiki_titles_resolve_local_assets(self):
        baseline = snapshot(candidate("bwiki", "Gone.png"))
        baseline["candidates"][0]["file_title"] = "文件:Gone.png"
        report = check.compare(baseline, snapshot(), self.manifest)
        self.assertEqual(report["removed"][0]["local_path"], "assets/public/wikis/bwiki/Gone--def.png")
        self.assertIn("SHA-1 曾不一致", check.render(report))

    def test_duplicate_candidates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            check.compare(snapshot(candidate("fandom", "X.png"), candidate("fandom", "X.png")), snapshot(), {})

    def test_render_escapes_cells_and_limits_rows(self):
        current = snapshot(candidate("fandom", "A|B.png", song_titles=["Song|One"]), candidate("fandom", "C.png"))
        report = check.compare(snapshot(), current, {})
        with patch.object(check, "ROW_LIMIT", 1):
            markdown = check.render(report)
        self.assertIn("[A\\|B.png](https://example.test/wiki/File:A\\|B.png)", markdown)
        self.assertIn("Song\\|One", markdown)
        self.assertIn("另有 1 项未列出", markdown)
        self.assertIn("fetch_wikis.py --resume", markdown)
        self.assertNotIn("C.png", markdown.split("另有")[0])

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
            self.assertTrue(summary["changed"])
            self.assertEqual(summary["counts"]["added"], 1)
            self.assertIn("### 新增（1）", (root / "report.md").read_text(encoding="utf-8"))
            self.assertIn('"changed": true', printed.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
