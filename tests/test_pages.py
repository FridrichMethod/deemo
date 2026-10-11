"""Ensure only tracked, allowlisted website files reach the GitHub Pages payload."""

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_pages", ROOT / "scripts/prepare_pages.py")
pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pages)


def workflow_jobs(relative):
    """{job id: its lines} of a workflow, split at the two-space job keys under `jobs:` (the layout these files use)."""
    lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()
    body = lines[lines.index("jobs:") + 1 :]
    starts = [index for index, line in enumerate(body) if re.fullmatch(r"  [A-Za-z_][\w-]*:", line)]
    return {
        body[start].strip().rstrip(":"): body[start + 1 : end]
        for start, end in zip(starts, [*starts[1:], len(body)], strict=True)
    }


class WorkflowTests(unittest.TestCase):
    def test_only_main_is_ever_deployed(self):
        # workflow_dispatch runs on any ref the maintainer picks; the deploy job itself refuses all but main, whatever
        # the github-pages environment's branch policy would allow.
        jobs = workflow_jobs(".github/workflows/pages.yml")
        deployers = [
            name for name, lines in jobs.items() if any("uses: actions/deploy-pages@" in line for line in lines)
        ]
        self.assertEqual(deployers, ["deploy"])
        self.assertIn("    if: github.ref == 'refs/heads/main'", jobs["deploy"])


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        self.output = Path(self.temporary.name) / "site"
        self.tracked = set(pages.PUBLIC_FILES)
        for relative in self.tracked:
            (self.root / relative).write_bytes(b"public")

    def add(self, relative, tracked=True):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample")
        if tracked:
            self.tracked.add(relative)

    def test_only_tracked_allowlisted_files_are_copied(self):
        self.add("assets/public/art.png")
        self.add("data/catalog.js")
        self.add("scripts/build_catalog.py")
        self.add(".github/workflows/pages.yml")
        self.add(".git/config")
        self.add("assets/.env")
        self.add("src/__pycache__/cached.pyc")
        self.add("assets/local-only.png", tracked=False)
        result = pages.prepare(self.root, self.output, self.tracked)
        actual = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        expected = pages.PUBLIC_FILES | {"assets/public/art.png", "data/catalog.js"}
        self.assertEqual(actual, expected)
        self.assertEqual(result["files"], len(expected))
        for name in actual:
            self.assertEqual((self.output / name).read_bytes(), (self.root / name).read_bytes())

    def test_missing_entry_file_fails(self):
        self.tracked.remove("index.html")
        with self.assertRaisesRegex(ValueError, "Required public files"):
            pages.prepare(self.root, self.output, self.tracked)

    def test_symlinks_are_rejected(self):
        (self.root / "assets").mkdir()
        (self.root / "assets/leak").symlink_to(self.root / "NOTICE")
        self.tracked.add("assets/leak")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            pages.prepare(self.root, self.output, self.tracked)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            pages.prepare(self.root, self.output, self.tracked)

    def test_repository_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            pages.prepare(self.root, self.root / "site", self.tracked)

    def test_oversized_payload_is_rejected_before_copy(self):
        with patch.object(pages, "MAX_SITE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "budget"):
                pages.prepare(self.root, self.output, self.tracked)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
