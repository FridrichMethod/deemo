"""Every catalog token the archive page labels (source status, file quality, provenance class) has an English and a
Chinese label."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)


def message_tables():
    return json.loads(REGISTER.search((ROOT / "src/i18n/archive.js").read_text(encoding="utf-8")).group(1))


class ArchiveLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
        cls.records = [record for asset in cls.catalog["assets"] for record in asset["provenance"]]
        cls.tables = message_tables()

    def assert_labelled(self, prefix, tokens):
        self.assertTrue(tokens, f"No {prefix} tokens found in data/catalog.json")
        for language, table in self.tables.items():
            missing = sorted(token for token in tokens if f"{prefix}.{token}" not in table)
            self.assertEqual(missing, [], f"Add {prefix}.<token> labels for {language} to src/i18n/archive.js; the page would show raw tokens")

    def test_source_statuses_are_labelled(self):
        self.assert_labelled("status", {source["status"] for source in self.catalog["sources"]})

    def test_file_qualities_are_labelled(self):
        self.assert_labelled("quality", {record["quality"] for record in self.records if record.get("quality")})

    def test_provenance_classes_are_labelled(self):
        # Archives records carry a class token; every wiki upload carries the same lineage caveat, labelled "wiki".
        classes = {"wiki" if record["family"] == "wikis" else record["provenance"]
                   for record in self.records if isinstance(record.get("provenance"), str)}
        self.assert_labelled("provenance.class", classes)


if __name__ == "__main__":
    unittest.main()
