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
from unittest.mock import patch

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


def image_info(width, height, name="image.png"):
    return {"width": width, "height": height, "size": 1000, "mime": "image/png", "sha1": "0" * 40,
            "url": f"https://images.example.test/{name}", "descriptionurl": f"https://wiki.example.test/{name}",
            "timestamp": "2021-09-01T00:00:00Z"}


def allimage(name, width, height):
    """A BWIKI allimages row: underscored name, page title with spaces."""
    return {**image_info(width, height, name), "name": name, "title": "文件:" + name.replace("_", " ")}


def song(title, collections=(), source="fandom"):
    return {"title": title, "page_url": f"https://{source}.example.test/{title}", "source_id": fetch.SOURCES[source]["id"],
            "collections": list(collections)}


def discover_bwiki(inventory, fandom_songs=(), bwiki_pages=()):
    """Run BWIKI discovery against a canned allimages inventory and canned song pages."""
    with patch.object(fetch, "category", return_value=[]), patch.object(fetch, "pages", return_value=list(bwiki_pages)), \
            patch.object(fetch, "allimages", return_value=list(inventory)):
        return fetch.discover_bwiki(list(fandom_songs))


class DiscoveryFilterTests(unittest.TestCase):
    def test_bwiki_skips_ui_sprites_like_the_fandom_path(self):
        inventory = [allimage("Exotic_Collections_Titletab.png", 70, 47),  # imported as a "cover" on 2026-10-08
                     allimage("DEEMO_II_collection.png", 512, 512),
                     allimage("20210901_collection.png", 512, 512),  # ^\d{8}  only matches the spaced title form
                     allimage("Etude_collection.png", 180, 180),
                     allimage("Magnolia.png", 1024, 1024)]
        selected, _, stats = discover_bwiki(inventory, [song("Magnolia", ["Etude Collection"])])
        self.assertEqual({row["info"]["name"]: row["kind"] for row in selected},
                         {"Etude_collection.png": "collection_cover", "Magnolia.png": "song_art"})
        self.assertEqual(stats["excluded_large_images"], [])

    def test_bwiki_collection_covers_need_a_minimum_edge(self):
        selected, _, _ = discover_bwiki([allimage("Spring_selection.png", 180, 142), allimage("Tiny_collection.png", 99, 300)])
        self.assertEqual([row["info"]["name"] for row in selected], ["Spring_selection.png"])

    def test_fandom_skips_icon_sized_collection_covers(self):
        page = {"title": "Song A", "pageid": 1, "fullurl": "https://fandom.example.test/Song_A", "categories": [],
                "revisions": [{"slots": {"main": {"*": "{{Song|img=Song A.png}}{{Return|Etude Collection}}"}}}],
                "images": [{"title": "File:Song A.png"}, {"title": "File:Tiny booksprite.png"},
                           {"title": "File:Etude booksprites.png"}]}
        infos = {"File:Song A.png": image_info(1024, 1024), "File:Tiny booksprite.png": image_info(64, 64),
                 "File:Etude booksprites.png": image_info(512, 512)}
        with patch.object(fetch, "category", lambda source, title: [{"title": "Song A", "ns": 0}] if title == "Category:Songs" else []), \
                patch.object(fetch, "pages", lambda source, titles: [page] if titles == ["Song A"] else []), \
                patch.object(fetch, "imageinfo", lambda source, titles: infos):
            selected, _, stats = fetch.discover_fandom()
        self.assertEqual({row["file_title"]: row["kind"] for row in selected},
                         {"File:Song A.png": "song_art", "File:Etude booksprites.png": "collection_cover"})
        self.assertEqual([row["title"] for row in stats["excluded"]], ["File:Tiny booksprite.png"])


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
