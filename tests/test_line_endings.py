"""Inherited files pinned in data/legacy-inventory.json keep their exact bytes on every checkout.

With core.autocrlf=true (the Git for Windows default) or core.eol=crlf, Git rewrites LF as CRLF in
files it treats as text when it checks them out, and the byte-for-byte inventory checks then fail
on a fresh clone. .gitattributes therefore exempts the pinned files from line-ending conversion.
These tests ask Git for each pinned blob exactly as such a checkout would write it.
"""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CRLF_CHECKOUTS = (("core.autocrlf=true",), ("core.autocrlf=false", "core.eol=crlf"))


def git(*args, stdin=None):
    return subprocess.run(["git", "-C", str(ROOT), *args], input=stdin, capture_output=True, check=True).stdout


def in_git_checkout():
    if not shutil.which("git"):
        return False
    try:
        return Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() == ROOT
    except subprocess.CalledProcessError:
        return False


@unittest.skipUnless(in_git_checkout(), "needs git and a Git checkout of this repository")
class LineEndingTests(unittest.TestCase):
    rows = json.loads((ROOT / "data/legacy-inventory.json").read_text(encoding="utf-8"))["files"]

    def test_pinned_files_are_exempt_from_line_ending_conversion(self):
        paths = [row["path"] for row in self.rows]
        fields = git("check-attr", "-z", "--stdin", "text", stdin="\0".join(paths).encode()).split(b"\0")[:-1]
        converted = [path.decode() for path, _, value in zip(fields[::3], fields[1::3], fields[2::3]) if value != b"unset"]
        self.assertEqual(converted, [])
        self.assertEqual(len(fields), 3 * len(paths))

    def test_crlf_checkouts_keep_pinned_bytes(self):
        for settings in CRLF_CHECKOUTS:
            options = [part for setting in settings for part in ("-c", setting)]
            changed = [row["path"] for row in self.rows
                       if hashlib.sha256(git(*options, "cat-file", "--filters", f"--path={row['path']}", row["git_blob"])).hexdigest()
                       != row["sha256"]]
            with self.subTest(settings=settings):
                self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
