"""Check the bilingual page strings and the paired English / Simplified Chinese documents."""

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "zh-CN")
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
REGISTER = re.compile(r"DEEMO_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Literal keys only; dynamic keys such as t(`kind.${kind}`) are checked by prefix.
T_CALL = re.compile(r"""\bt\(\s*(["'])([^"'\n]+)\1\s*[,)]""")
T_PREFIX = re.compile(r"""\bt\(\s*(?:`([\w.-]+)\$\{|(["'])([\w.-]+)\2\s*\+)""")
# Entry pages and the scripts whose t() calls they run (inline scripts are read from the page itself).
PAGES = {"archive.html": ["src/archive.js"], "templates/slideshow.html": []}
DOCUMENTS = [
    "README.md", "docs/sources-archives.md", "docs/sources-artists.md", "docs/sources-wikis.md",
    "licenses/README.md", "scripts/legacy/README.md",
]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def counterpart(relative):
    path = Path(relative)
    return path.with_name(path.stem + ".zh-CN.md").as_posix()


def message_tables(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    match = REGISTER.search(text)
    if not match:
        raise AssertionError(f"{relative} must end with DEEMO_I18N.register({{...strict JSON...}});")
    return json.loads(match.group(1))


class Page(HTMLParser):
    """Collect scripts, translation keys and Han text outside zh-tagged subtrees (comments are ignored)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.keys, self.han, self.stack = [], set(), [], []
        self.root_lang = None
        self.in_script = False

    def exempt(self):
        return any(exempt for _, exempt in self.stack)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.root_lang = attributes.get("lang")
        is_zh = tag != "html" and (attributes.get("lang") or "").lower().startswith("zh")
        if not (self.exempt() or is_zh):
            self.han.extend(f"<{tag} {name}>: {value}" for name, value in attrs if value and HAN.search(value))
        if attributes.get("data-i18n"):
            self.keys.add(attributes["data-i18n"])
        for pair in (attributes.get("data-i18n-attr") or "").split(";"):
            if ":" in pair:
                self.keys.add(pair.split(":", 1)[1].strip())
        if tag == "script":
            self.in_script = True
            self.scripts.append(("src", attributes["src"]) if attributes.get("src") else ("inline", ""))
        if tag not in VOID:
            self.stack.append((tag, is_zh))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.in_script and self.scripts and self.scripts[-1][0] == "inline":
            self.scripts[-1] = ("inline", self.scripts[-1][1] + data)
        if not self.exempt() and HAN.search(data):
            self.han.append(data.strip()[:80])


def parse_page(relative):
    page = Page()
    page.feed((ROOT / relative).read_text(encoding="utf-8-sig"))
    return page


def links(text):
    """Relative markdown link targets outside fenced code blocks."""
    targets, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        for target in MARKDOWN_LINK.findall(line):
            parts = urlsplit(target)
            if not parts.scheme and not parts.netloc and parts.path:
                targets.append(unquote(parts.path))
    return targets


def structure(text):
    headings = fences = rows = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fences += 1
        elif fences % 2 == 0 and re.match(r"#{1,6} ", stripped):
            headings += 1
        elif fences % 2 == 0 and stripped.startswith("|"):
            rows += 1
    return {"headings": headings, "code fences": fences, "table rows": rows}


class MessageTests(unittest.TestCase):
    def test_every_message_file_pairs_languages_and_placeholders(self):
        files = sorted((ROOT / "src/i18n").glob("*.js"))
        self.assertIn(ROOT / "src/i18n/common.js", files)
        seen = {}
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(file=relative):
                tables = message_tables(relative)
                self.assertEqual(set(tables), set(LANGUAGES))
                self.assertEqual(set(tables["en"]), set(tables["zh-CN"]), "English and Chinese keys differ")
                for key, english in tables["en"].items():
                    chinese = tables["zh-CN"][key]
                    self.assertTrue(english.strip() and chinese.strip(), f"Empty string: {key}")
                    self.assertEqual(set(PLACEHOLDER.findall(english)), set(PLACEHOLDER.findall(chinese)), f"Placeholder mismatch: {key}")
                    self.assertNotIn(key, seen, f"{key} is defined in both {seen.get(key)} and {relative}")
                    seen[key] = relative

    def test_message_files_are_all_loaded_by_a_page(self):
        loaded = {src for page in PAGES for kind, src in parse_page(page).scripts if kind == "src"}
        for path in sorted((ROOT / "src/i18n").glob("*.js")):
            self.assertIn(path.relative_to(ROOT).as_posix(), loaded)


class PageTests(unittest.TestCase):
    def check_page(self, relative):
        page = parse_page(relative)
        self.assertEqual(page.root_lang, "en", "English is the default document language")
        sources = [src for kind, src in page.scripts if kind == "src"]
        self.assertIn("src/i18n.js", sources)
        self.assertIn("src/i18n/common.js", sources)
        runtime = sources.index("src/i18n.js")
        tables_loaded = [src for src in sources if src.startswith("src/i18n/")]
        for src in tables_loaded:
            self.assertGreater(sources.index(src), runtime, f"{src} must load after src/i18n.js")
        # Code that calls t() runs after every message file has registered its strings.
        last_table = max(page.scripts.index(("src", src)) for src in tables_loaded)
        for index, (kind, value) in enumerate(page.scripts):
            if (kind == "src" and value in PAGES[relative]) or (kind == "inline" and T_CALL.search(value)):
                self.assertGreater(index, last_table, f"{value[:40]!r} runs before the message files in {relative}")
        keys = set()
        for src in tables_loaded:
            keys |= set(message_tables(src)["en"])
        code = "\n".join(text for kind, text in page.scripts if kind == "inline")
        code += "\n".join((ROOT / script).read_text(encoding="utf-8") for script in PAGES[relative])
        used = page.keys | {match[1] for match in T_CALL.findall(code)}
        self.assertEqual(sorted(used - keys), [], f"Keys used by {relative} but not defined")
        for match in T_PREFIX.findall(code):
            prefix = match[0] or match[2]
            self.assertTrue(any(key.startswith(prefix) for key in keys), f"No keys start with {prefix!r}")
        self.assertEqual(page.han, [], f"Untranslated Han text in {relative}; tag it lang=\"zh-CN\" or move it to src/i18n/")
        toggles = len(re.findall(r"data-lang-toggle", (ROOT / relative).read_text(encoding="utf-8-sig")))
        self.assertGreaterEqual(toggles, 1, f"{relative} needs a language toggle")

    def test_archive_page(self):
        self.check_page("archive.html")

    def test_slideshow_template(self):
        self.check_page("templates/slideshow.html")

    def test_scripts_hold_no_hardcoded_chinese(self):
        for relative in ["src/i18n.js", *sorted({script for scripts in PAGES.values() for script in scripts})]:
            with self.subTest(script=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(HAN.findall(text), [], f"Move Chinese strings in {relative} to src/i18n/")

    def test_manifest_name_is_english(self):
        manifest = json.loads((ROOT / "site.webmanifest").read_text(encoding="utf-8"))
        for field in ("name", "short_name"):
            self.assertFalse(HAN.search(manifest[field]), field)


class DocumentTests(unittest.TestCase):
    def test_documents_have_chinese_counterparts_with_switchers(self):
        for english in DOCUMENTS:
            chinese = counterpart(english)
            with self.subTest(document=english):
                self.assertTrue((ROOT / chinese).is_file(), f"Missing {chinese}")
                head_en = "\n".join((ROOT / english).read_text(encoding="utf-8").splitlines()[:6])
                head_zh = "\n".join((ROOT / chinese).read_text(encoding="utf-8").splitlines()[:6])
                self.assertIn(f"]({Path(chinese).name})", head_en, "English page must link to its Chinese version near the top")
                self.assertIn(f"]({Path(english).name})", head_zh, "Chinese page must link back to English near the top")

    def test_relative_links_resolve_and_stay_in_language(self):
        translated = {(ROOT / english).resolve(): (ROOT / counterpart(english)).resolve() for english in DOCUMENTS}
        for english in DOCUMENTS:
            if not (ROOT / counterpart(english)).is_file():
                continue  # Reported by the counterpart test above.
            for relative, is_chinese in ((english, False), (counterpart(english), True)):
                path = ROOT / relative
                own_counterpart = translated[(ROOT / english).resolve()] if not is_chinese else (ROOT / english).resolve()
                for target in links(path.read_text(encoding="utf-8")):
                    with self.subTest(document=relative, link=target):
                        resolved = (path.parent / target).resolve()
                        self.assertTrue(resolved.is_relative_to(ROOT), "Link escapes the repository")
                        self.assertTrue(resolved.exists(), "Broken relative link")
                        if resolved == own_counterpart:
                            continue
                        if is_chinese:
                            self.assertNotIn(resolved, translated, "Chinese page should link to the .zh-CN.md version")
                        else:
                            self.assertFalse(resolved.name.endswith(".zh-CN.md"), "English page should link to the English version")

    def test_translations_keep_document_structure(self):
        for english in DOCUMENTS:
            if not (ROOT / counterpart(english)).is_file():
                continue  # Reported by the counterpart test above.
            with self.subTest(document=english):
                self.assertEqual(
                    structure((ROOT / english).read_text(encoding="utf-8")),
                    structure((ROOT / counterpart(english)).read_text(encoding="utf-8")),
                )


if __name__ == "__main__":
    unittest.main()
