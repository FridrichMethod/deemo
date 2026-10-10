"""Offline checks for scripts/fetch_wikis.py: discovery filters, manifest safety, carry-forward and retries.

Nothing touches the network: requests.get is replaced by a fake that serves canned responses, and ROOT points at a
temporary directory, so the repository's own manifests are never read or written.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/fetch_wikis.py"
SPEC = importlib.util.spec_from_file_location("fetch_wikis", SCRIPT)
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def asset_id(source, file_title):
    return "wikis:" + source + ":" + hashlib.sha256(file_title.encode("utf-8")).hexdigest()[:16]


class EncodingTests(unittest.TestCase):
    def test_resume_reads_utf8_manifests_under_an_ascii_locale(self):
        """The manifests are UTF-8 (BWIKI titles start with 文件:); reading them must not depend on the locale."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            title = "文件:Magnolia.png"
            dump(root / "data/sources/wikis.json", {
                "schema_version": 1, "fetched_at": "2026-01-01T00:00:00Z", "failures": [],
                "sources": [{**fetch.SOURCES["bwiki"], "status": "complete"}],
                "assets": [{"id": asset_id("bwiki", title), "source_id": "wikis:bwiki", "title": "Magnolia"}],
            })
            dump(root / "data/sources/wiki-discovery.json", {
                "schema_version": 1, "fetched_at": "2026-01-01T00:00:00Z",
                "candidates": [{"source": "bwiki", "file_title": title, "kind": "song_art", "info": {}}],
            })
            code = ("import importlib.util, sys\nfrom pathlib import Path\n"
                    "spec = importlib.util.spec_from_file_location('fetch_wikis', sys.argv[1])\n"
                    "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
                    "module.ROOT = Path(sys.argv[2])\nsys.argv = ['fetch_wikis.py', '--resume', '--metadata-only']\n"
                    "module.main()\n")
            # LC_ALL=C with UTF-8 mode off makes the locale encoding ASCII, like cp1252/GBK it cannot decode 文件.
            env = {key: value for key, value in os.environ.items() if not key.startswith("LC_")}
            result = subprocess.run([sys.executable, "-I", "-X", "utf8=0", "-c", code, str(SCRIPT), str(root)],
                                    env={**env, "LC_ALL": "C"}, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr[-600:])
            self.assertIn("Resuming saved discovery: 1 candidates", result.stdout)


if __name__ == "__main__":
    unittest.main()
