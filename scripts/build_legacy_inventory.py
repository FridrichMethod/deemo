"""Verify the byte-preserving relocation of inherited files.

The committed inventory works without Git. --write recreates it from the
pre-migration commit and refuses any changed file contents.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "193055eb9df7f8aa1197d413810f59b6bf4eb9a4"
INVENTORY = ROOT / "data/legacy-inventory.json"
ROOT_ICONS = {
    "android-chrome-192x192.png", "apple-touch-icon.png", "favicon-16x16.png",
    "favicon-32x32.png", "favicon.ico", "mstile-150x150.png", "safari-pinned-tab.svg",
}
# Inherited from upstream but deliberately not redistributed: commercial fonts whose licenses forbid
# it (see NOTICE). They map to no destination and must not reappear at either path.
WITHDRAWN = {
    "src/COPRGTL.ttf": "assets/site/fonts/COPRGTL.ttf",
    "src/RocknRoll_Typo_bold.ttf": "assets/site/fonts/RocknRoll_Typo_bold.ttf",
}


def destination(original):
    if original in WITHDRAWN:
        return None
    path = Path(original)
    if original.startswith("trans/"):
        return "assets/legacy/" + original, "legacy_original"
    if original.startswith("tiny/"):
        return "assets/legacy/" + original, "legacy_quantized"
    if original in ROOT_ICONS or (path.parent == Path("src") and path.suffix == ".svg"):
        return "assets/site/icons/" + path.name, "site_icon"
    if path.parent == Path("src") and path.suffix == ".ttf":
        return "assets/site/fonts/" + path.name, "site_font"
    if path.parent == Path("src") and path.suffix == ".mp3":
        return "assets/site/audio/" + path.name, "site_audio"
    fixed = {
        "src/bg.png": ("assets/site/images/bg.png", "site_image"),
        "src/Capture.PNG": ("assets/site/images/upstream-preview-upper.png", "historical_preview"),
        "src/Capture.png": ("assets/site/images/upstream-preview-lower.png", "historical_preview"),
        "src/main.js": ("src/vendor/legacy-ui.js", "vendor_software"),
        "LICENSE": ("LICENSE", "software_license"),
        "docs/licenses/DeemoSongs-MIT.txt": ("licenses/DeemoSongs-MIT.txt", "data_license"),
    }
    if original in fixed:
        return fixed[original]
    if original.startswith("pil/"):
        return "scripts/legacy/" + path.name, "historical_tool"
    return None


def create():
    tree = subprocess.check_output(["git", "ls-tree", "-rz", "--full-tree", SOURCE_COMMIT], cwd=ROOT)
    files = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        info, original = entry.split(b"\t", 1)
        original = original.decode("utf-8")
        target = destination(original)
        if not target:
            continue
        path, category = target
        raw = (ROOT / path).read_bytes()
        oid = info.split()[2].decode("ascii")
        algorithm = "sha1" if len(oid) == 40 else "sha256"
        actual_oid = hashlib.new(algorithm, f"blob {len(raw)}\0".encode() + raw).hexdigest()
        if actual_oid != oid:
            raise ValueError(f"Relocation changed file contents: {original} -> {path}")
        files.append({"original_path": original, "path": path, "category": category,
                      "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "git_blob": oid})
    return {"schema_version": 1, "source_commit": SOURCE_COMMIT,
            "source_repository": "https://github.com/FridrichMethod/deemo",
            "files": sorted(files, key=lambda row: row["original_path"])}


def verify(inventory):
    seen = set()
    for row in inventory["files"]:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe inventory path: {relative}")
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError(f"Inventory path outside repository: {relative}")
        if row["path"].casefold() in seen:
            raise ValueError(f"Duplicate or case-colliding inventory path: {relative}")
        seen.add(row["path"].casefold())
        raw = path.read_bytes()
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"Inventory hash mismatch: {relative}")
        if row["original_path"] != row["path"] and (ROOT / row["original_path"]).exists():
            raise ValueError(f"Old path still exists: {row['original_path']}")
    for original, former in WITHDRAWN.items():
        if any(row["original_path"] == original for row in inventory["files"]) or (ROOT / original).exists() or (ROOT / former).exists():
            raise ValueError(f"Withdrawn file is back in the repository: {original}")
    print(f"Verified {len(seen)} inherited files: bytes, hashes, paths and case-insensitive uniqueness.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Rebuild inventory and compare every file to its original Git blob")
    args = parser.parse_args()
    if args.write:
        inventory = create()
        verify(inventory)
        INVENTORY.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        verify(json.loads(INVENTORY.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
