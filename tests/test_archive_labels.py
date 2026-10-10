"""Every catalog token the archive page labels (source status, file quality, provenance class, upstream status) has an
English and a Chinese label, and every provenance-record field is either shown by the page or deliberately left out."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
# Source statuses the fetch scripts write besides fetch_archives' fixed list. A refetch can bring any of them into the
# catalog (a run with failures marks its source partial or failed), so they are labelled before they first appear.
FETCHER_STATUSES = {"complete", "partial", "failed", "unavailable", "discovered", "pending", "reference_only",
                    "fetched", "no_assets_fetched"}
# Marks a refetch puts on a record it keeps although upstream changed (removed, re-uploaded, or not re-downloadable).
UPSTREAM_STATUSES = {"removed", "superseded", "fetch_failed"}
# Provenance-record fields src/archive.js shows (viewer, card or search). File facts (path, size, digest, fetch time)
# are shown once per asset from the asset's own fields, which every record of a byte-identical file shares.
SHOWN = {
    "source_id", "source_name", "page_url", "download_url", "family", "kind", "title", "artist", "composer", "composer_source",
    "collection", "collection_scope", "collections", "collection_aliases", "song_titles", "internal_key", "notes", "quality", "provenance",
    "quality_notes", "source_dimensions", "source_dimensions_kind", "variant_note", "layout_note", "delivery_note", "rights", "rights_holder",
    "wiki_original_sha1_matches", "upstream_status", "superseded_by", "mapping_status", "title_status", "variants",
    "path", "width", "height", "format", "bytes", "sha256", "fetched_at",
}
# Fields the page leaves out on purpose, with the reason. A new manifest field fails the test until it is added to one
# of the two lists, so a caveat cannot be dropped silently.
IGNORED = {
    "id": "record id; links use the asset id",
    "game": "always DEEMO, the catalog's scope",
    "mode": "Pillow image mode, a technical detail of the file",
    "transformed": "false for every record (checked below): files are kept as downloaded",
    "api_approved": "Cover Art Archive approval, true for every scan (checked below)",
    "published_at": "post date; the source link leads to the post",
    "source_page_index": "position of the image in its source post",
    "source_caption": "the artist's caption the shown title was mapped from",
    "advertised_lightbox_url": "lightbox URL behind source_dimensions_kind, which is shown",
    "resolved_url": "redirect target of the shown download URL",
    "resolved_download_url": "redirect target of the shown download URL",
    "wiki_original_url": "the wiki's original-file URL; the URL actually downloaded is shown",
    "content_type": "MIME type; the file format is shown",
    "wiki_mime": "MIME type; the file format is shown",
    "wiki_sha1": "digest behind wiki_original_sha1_matches, which is shown when false",
    "download_sha1": "digest behind wiki_original_sha1_matches, which is shown when false",
    "wiki_original_size_matches": "false only with wiki_original_sha1_matches false (checked below), which is shown",
    "wiki_timestamp": "upload time on the wiki",
    "wiki_extmetadata": "MediaWiki upload metadata (date, object name); no creator or license fields",
    "related_pages": "wiki pages that use the file; the source page is shown",
    "mapping_method": "how the shown song titles were matched",
    "mapping_source": "where the shown song titles were matched from",
    "download_attempt_failures": "failed attempts before the download that succeeded",
    "scan_types": "scan side, already part of the shown title",
    "tags": "Tumblr post tags the shown title was taken from",
}


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
        self.assert_labelled("status", {source["status"] for source in self.catalog["sources"]} | FETCHER_STATUSES)

    def test_file_qualities_are_labelled(self):
        self.assert_labelled("quality", {record["quality"] for record in self.records if record.get("quality")})

    def test_provenance_classes_are_labelled(self):
        # Archives records carry a class token; every wiki upload carries the same lineage caveat, labelled "wiki".
        classes = {"wiki" if record["family"] == "wikis" else record["provenance"]
                   for record in self.records if isinstance(record.get("provenance"), str)}
        self.assert_labelled("provenance.class", classes)

    def test_dimension_kinds_are_labelled(self):
        # source_dimensions_kind is "<token>; <maintainer prose>"; the page states the advertised size through the token's
        # label and shows the raw value only when the label or the size is missing.
        records = [record for record in self.records if record.get("source_dimensions_kind")]
        self.assert_labelled("provenance.dimensions_kind",
                             {record["source_dimensions_kind"].split(";")[0].strip() for record in records})
        for record in records:
            dimensions = record.get("source_dimensions")
            self.assertTrue(isinstance(dimensions, list) and len(dimensions) == 2 and all(
                isinstance(value, int) and value > 0 for value in dimensions), record["id"])

    def test_upstream_statuses_are_labelled(self):
        found = {record["upstream_status"] for record in self.records if record.get("upstream_status")}
        self.assert_labelled("upstream", found | UPSTREAM_STATUSES)

    def test_every_provenance_field_is_shown_or_ignored(self):
        self.assertEqual(SHOWN & set(IGNORED), set())
        families = {}
        for record in self.records:
            families.setdefault(record["family"], set()).update(record)
        for family, keys in sorted(families.items()):
            with self.subTest(family=family):
                self.assertEqual(sorted(keys - SHOWN - set(IGNORED)), [],
                                 "Show these fields in src/archive.js or list them in IGNORED with the reason")

    def test_shown_fields_are_read_by_the_page(self):
        script = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        unread = sorted(key for key in SHOWN if not re.search(rf'\.{key}\b|"{key}"', script))
        self.assertEqual(unread, [], "src/archive.js no longer reads these SHOWN fields")

    def test_ignored_caveats_stay_harmless(self):
        self.assertFalse([record["id"] for record in self.records if record.get("transformed")], "transformed files")
        self.assertFalse([record["id"] for record in self.records if record.get("api_approved") is False], "unapproved scans")
        self.assertFalse([record["id"] for record in self.records if record.get("wiki_original_size_matches") is False
                          and record.get("wiki_original_sha1_matches") is not False], "size mismatch with a matching digest")


if __name__ == "__main__":
    unittest.main()
