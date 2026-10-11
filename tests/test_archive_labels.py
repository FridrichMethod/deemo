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
FETCHER_STATUSES = {
    "complete",
    "partial",
    "failed",
    "unavailable",
    "discovered",
    "pending",
    "reference_only",
    "fetched",
    "no_assets_fetched",
}
# Marks a refetch puts on a record it keeps although upstream changed (removed, re-uploaded, or not re-downloadable).
UPSTREAM_STATUSES = {"removed", "superseded", "fetch_failed"}
# Provenance-record fields src/archive.js shows (viewer, card or search). File facts (path, size, digest, fetch time)
# are shown once per asset from the asset's own fields, which every record of a byte-identical file shares.
SHOWN = {
    "source_id",
    "source_name",
    "page_url",
    "download_url",
    "family",
    "kind",
    "title",
    "artist",
    "composer",
    "composer_source",
    "collection",
    "collection_scope",
    "collections",
    "collection_aliases",
    "song_titles",
    "internal_key",
    "notes",
    "quality",
    "provenance",
    "quality_notes",
    "source_dimensions",
    "source_dimensions_kind",
    "variant_note",
    "layout_note",
    "delivery_note",
    "rights",
    "rights_holder",
    "wiki_original_sha1_matches",
    "upstream_status",
    "superseded_by",
    "mapping_status",
    "title_status",
    "variants",
    "path",
    "width",
    "height",
    "format",
    "bytes",
    "sha256",
    "fetched_at",
}
# Where src/archive.js shows the SHOWN fields: the functions that show them (with the helpers below that they call), and
# the fields each set must read. A field read only elsewhere (by a card or the search, say) does not reach the viewer.
SHOWN_BY = {
    # Each record's block in the viewer.
    ("provenanceBlock",): {
        "source_id",
        "source_name",
        "page_url",
        "download_url",
        "title",
        "artist",
        "composer",
        "composer_source",
        "collection",
        "collection_scope",
        "collections",
        "song_titles",
        "notes",
        "quality",
        "provenance",
        "quality_notes",
        "source_dimensions",
        "source_dimensions_kind",
        "variant_note",
        "layout_note",
        "delivery_note",
        "rights",
        "rights_holder",
        "wiki_original_sha1_matches",
        "upstream_status",
        "superseded_by",
        "mapping_status",
        "title_status",
        "variants",
    },
    # The file facts, once per asset, in the viewer.
    ("open", "localizeViewer"): {"path", "width", "height", "format", "bytes", "sha256", "fetched_at"},
    # The cards: title, size line, source line and tag.
    ("more", "localizeCard"): {
        "title",
        "artist",
        "source_id",
        "source_name",
        "family",
        "kind",
        "width",
        "height",
        "format",
        "bytes",
    },
    # The search index.
    ("searchable",): {
        "title",
        "artist",
        "composer",
        "collection",
        "collections",
        "collection_aliases",
        "song_titles",
        "source_name",
        "page_url",
        "internal_key",
    },
    # The source and type filters.
    ("filter",): {"family", "kind"},
}
# Helpers that read record fields for the functions above; a helper's code counts only where it is called.
HELPERS = ("composerLines", "upstreamNotes", "dimensionsNote", "provenanceClass", "sourceName")
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


def definition(script, name):
    """The code of a function in src/archive.js: a declaration's body, or the expression of an arrow function."""
    declaration = re.search(rf"\bfunction {name}\(", script)
    if declaration is None:
        arrow = re.search(rf"\bconst {name} = \([^)]*\) => ([^\n]*)", script)
        if arrow is None:
            raise AssertionError(f"src/archive.js defines no function {name}")
        return arrow.group(1)
    opening, depth = script.index("{", declaration.end()), 0
    for index in range(opening, len(script)):
        depth += {"{": 1, "}": -1}.get(script[index], 0)
        if depth == 0:
            return script[opening + 1 : index]
    raise AssertionError(f"function {name}() is not closed")


def shown_code(script, functions):
    """The code of these functions and of the HELPERS they call, also in a spread (...upstreamNotes(p)), but not the
    methods of the same name (i18n.sourceName)."""
    code = "".join(definition(script, name) for name in functions)
    called = [name for name in HELPERS if re.search(rf"(?:\.\.\.|(?<![\w.$])){name}\(", code)]
    return code + "".join(definition(script, name) for name in called)


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
            self.assertEqual(
                missing,
                [],
                f"Add {prefix}.<token> labels for {language} to src/i18n/archive.js; the page would show raw tokens",
            )

    def test_source_statuses_are_labelled(self):
        self.assert_labelled("status", {source["status"] for source in self.catalog["sources"]} | FETCHER_STATUSES)

    def test_file_qualities_are_labelled(self):
        self.assert_labelled("quality", {record["quality"] for record in self.records if record.get("quality")})

    def test_provenance_classes_are_labelled(self):
        # Archives records carry a class token; every wiki upload carries the same lineage caveat, labelled "wiki".
        classes = {
            "wiki" if record["family"] == "wikis" else record["provenance"]
            for record in self.records
            if isinstance(record.get("provenance"), str)
        }
        self.assert_labelled("provenance.class", classes)

    def test_dimension_kinds_are_labelled(self):
        # source_dimensions_kind is "<token>; <maintainer prose>"; the page states the advertised size through the token's
        # label and shows the raw value only when the label or the size is missing.
        records = [record for record in self.records if record.get("source_dimensions_kind")]
        self.assert_labelled(
            "provenance.dimensions_kind", {record["source_dimensions_kind"].split(";")[0].strip() for record in records}
        )
        for record in records:
            dimensions = record.get("source_dimensions")
            self.assertTrue(
                isinstance(dimensions, list)
                and len(dimensions) == 2
                and all(isinstance(value, int) and value > 0 for value in dimensions),
                record["id"],
            )

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
                self.assertEqual(
                    sorted(keys - SHOWN - set(IGNORED)),
                    [],
                    "Show these fields in src/archive.js or list them in IGNORED with the reason",
                )

    def test_shown_fields_are_read_by_the_page(self):
        self.assertEqual(set().union(*SHOWN_BY.values()), SHOWN, "Say in SHOWN_BY where each SHOWN field is shown")
        script = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        for functions, fields in SHOWN_BY.items():
            with self.subTest(functions=functions):
                code = shown_code(script, functions)
                unread = sorted(key for key in fields if not re.search(rf'\.{key}\b|"{key}"', code))
                self.assertEqual(unread, [], f"src/archive.js no longer reads these SHOWN fields in {functions}")

    def test_shown_code_follows_only_the_helpers_called(self):
        script = (
            "const sourceName = (record) => i18n.sourceName(record.source_id, record.source_name);\n"
            "function composerLines(p) { return [p.composer_source]; }\n"
            "function provenanceBlock(p) { if (p.x) { block.append(sourceName(p)); } }\n"
            "function upstreamNotes(p) { return [p.superseded_by]; }\n"
            "function show(p) { block.append(...upstreamNotes(p)); }\n"
            "function localizeCard(card, asset) { i18n.sourceName(asset.id); return composerLines; }\n"
        )
        self.assertEqual(definition(script, "provenanceBlock"), " if (p.x) { block.append(sourceName(p)); } ")
        self.assertIn("record.source_id", shown_code(script, ["provenanceBlock"]))
        self.assertNotIn("composer_source", shown_code(script, ["provenanceBlock"]))
        self.assertNotIn("record.source_id", shown_code(script, ["localizeCard"]))
        self.assertNotIn("composer_source", shown_code(script, ["localizeCard"]))
        self.assertIn("p.superseded_by", shown_code(script, ["show"]))
        with self.assertRaisesRegex(AssertionError, "defines no function missing"):
            definition(script, "missing")

    def test_ignored_caveats_stay_harmless(self):
        self.assertFalse([record["id"] for record in self.records if record.get("transformed")], "transformed files")
        self.assertFalse(
            [record["id"] for record in self.records if record.get("api_approved") is False], "unapproved scans"
        )
        self.assertFalse(
            [
                record["id"]
                for record in self.records
                if record.get("wiki_original_size_matches") is False
                and record.get("wiki_original_sha1_matches") is not False
            ],
            "size mismatch with a matching digest",
        )


if __name__ == "__main__":
    unittest.main()
