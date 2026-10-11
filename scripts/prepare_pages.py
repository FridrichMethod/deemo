"""Stage only tracked, explicitly allowed public website files for GitHub Pages."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRECTORIES = {"assets", "data", "src", "docs", "licenses"}
PUBLIC_FILES = {
    ".nojekyll",
    "archive.html",
    "index.html",
    "site.webmanifest",
    "browserconfig.xml",
    "LICENSE",
    "NOTICE",
    "README.md",
    "README.zh-CN.md",
}
# Leave room for archive metadata below Pages' 1 GB published-site limit.
MAX_SITE_BYTES = 950_000_000


def select_files(root, tracked):
    selected = []
    for name in sorted(set(tracked)):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"Unsafe tracked path: {name}")
        if name not in PUBLIC_FILES:
            if relative.parts[0] not in PUBLIC_DIRECTORIES:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
        source = root / relative
        if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError(f"Symlinks must not be published: {name}")
        if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or out-of-repository public file: {name}")
        selected.append(relative)
    missing = PUBLIC_FILES - {path.as_posix() for path in selected}
    if missing:
        raise ValueError(f"Required public files are not tracked: {sorted(missing)}")
    return selected


def tracked_files(root):
    """Every Git-tracked path of the checkout at root, as git ls-files lists it."""
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return [name.decode("utf-8") for name in raw.split(b"\0") if name]


def payload(root, tracked):
    """The files a deployment publishes and their total size, measured without copying anything; fails over
    MAX_SITE_BYTES. tests/test_pages.py runs it on every push and pull request, before anything reaches main."""
    selected = select_files(root, tracked)
    total = sum((root / path).stat().st_size for path in selected)
    if total > MAX_SITE_BYTES:
        raise ValueError(f"Pages payload exceeds {MAX_SITE_BYTES} byte budget: {total}")
    return selected, total


def prepare(root, output, tracked):
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Pages output must be outside the repository")
    selected, total = payload(root, tracked)
    # Never overwrite an existing artifact or user directory.
    output.mkdir(parents=True, exist_ok=False)
    for relative in selected:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, destination)
    return {"files": len(selected), "bytes": total, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(ROOT, args.output, tracked_files(ROOT)), indent=2))


if __name__ == "__main__":
    main()
