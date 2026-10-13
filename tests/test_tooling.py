"""Keep the pinned code-quality tools consistent with each other, with the repository and with CI.

ruff, mypy and coverage.py (pyproject.toml), pre-commit (.pre-commit-config.yaml, requirements-lint.txt) and ESLint
(package.json, package-lock.json, eslint.config.mjs) are pinned in several files. These tests check that the pins
agree, that no tool reads or rewrites a byte-pinned or generated file, that ESLint only checks the published src/, and
that .github/workflows/tests.yml, which gates the Pages deployment, runs every tool. The standard library has no YAML
parser, so the YAML files are read line by line in the block layout they use; a line the readers do not recognise
fails the test instead of being skipped.
"""

import fnmatch
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no TOML reader; CI runs Python 3.12.
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
BOM = b"\xef\xbb\xbf"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
PINNED_ACTION = re.compile(
    r"(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<sha>[0-9a-f]{40})\s+#\s+(?P<tag>v\d+(?:\.\d+)*)"
)
LOCAL_WORKFLOW = re.compile(r"\./\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml")
REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[A-Za-z0-9._,-]+)\])?==(?P<version>[0-9][A-Za-z0-9.+!-]*)"
)
GRANTS_WRITE = re.compile(r"\s*(?:- )?[A-Za-z-]+:\s*['\"]?write(?:-all)?['\"]?\s*(?:#.*)?")

# Outputs of scripts/build_*.py, and a preview build_thumbnails.py may write next.
GENERATED = ("data/catalog.json", "data/catalog.js", "data/thumbs.json", "data/legacy-inventory.json", "index.html")
NEW_THUMB = "assets/thumbs/0123456789abcdef.webp"
# One of each kind of .gitattributes -text entry, for the checks that do not need Git.
PINNED_SAMPLES = (
    "LICENSE",
    "licenses/pace-MIT.txt",
    "src/vendor/legacy-ui.js",
    "scripts/legacy/pil.py",
    "assets/legacy/trans/sample.png",
    "assets/site/icons/sample.png",
)
SOURCES = (
    "scripts/build_catalog.py",
    "src/archive.js",
    "src/slideshow.css",
    "templates/slideshow.html",
    "README.md",
    "docs/sources-wikis.md",
    ".github/workflows/tests.yml",
    "tests/test_tooling.py",
)
# Nested paths that only resemble an excluded root path; the exclude is anchored at the repository root.
LOOKALIKES = ("docs/assets/notes.md", "tests/data/sample.json", "src/archive/index.html", "docs/LICENSE")
BOM_FILES = ("src/slideshow.css", "templates/slideshow.html")
LINTED_TREES = ("src/", "scripts/", "tests/", "templates/", "docs/", ".github/")
UNLINTED_TREES = ("src/vendor/", "scripts/legacy/")
MYPY_FILES = [
    "scripts/build_catalog.py",
    "scripts/build_legacy_inventory.py",
    "scripts/build_thumbnails.py",
    "scripts/check_sources.py",
    "scripts/prepare_pages.py",
]
# Hooks that would rewrite reviewed bytes (the BOMs, the JSON layout) or judge file modes, which Git on Windows lacks.
FORBIDDEN_HOOKS = {
    "fix-byte-order-marker",
    "pretty-format-json",
    "check-shebang-scripts-are-executable",
    "check-executables-have-shebangs",
}


def git(*args, stdin=None):
    return subprocess.run(["git", "-C", str(ROOT), *args], input=stdin, capture_output=True, check=True).stdout


def in_git_checkout():
    if not shutil.which("git"):
        return False
    try:
        return Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() == ROOT
    except subprocess.CalledProcessError:
        return False


def read_pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(relative):
    """{normalized name: (extras, version)} of a requirements file that holds only exact pins and comments."""
    pins = {}
    for number, line in enumerate((ROOT / relative).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        match = REQUIREMENT.fullmatch(line)
        if not match:
            raise AssertionError(f"{relative}:{number} is not an exact pin (name==version): {line!r}")
        name = normalize(match["name"])
        if name in pins:
            raise AssertionError(f"{relative}:{number} pins {name} twice")
        pins[name] = (frozenset(filter(None, (match["extras"] or "").split(","))), match["version"])
    return pins


def version_tuple(text):
    return tuple(int(part) for part in text.removeprefix("v").split("."))


def yaml_scalar(value, where):
    """A plain, quoted or flow-list YAML value as written in the configuration files."""
    if value.startswith("[") and value.endswith("]"):
        return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1]
    if " #" in value:
        raise AssertionError(f"{where}: inline comments are not supported here: {value!r}")
    return value


def read_pre_commit_config():
    """Top-level values (block scalars included) and repos of .pre-commit-config.yaml; unknown lines are errors."""
    lines = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8").splitlines()
    config, repos = {}, []
    repo = hook = key = None
    in_repos = False
    index = 0
    while index < len(lines):
        line, where = lines[index], f".pre-commit-config.yaml:{index + 1}"
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            match = re.fullmatch(r"([a-z_]+):(?: (.*))?", line)
            if not match:
                raise AssertionError(f"{where}: unrecognised line {line!r}")
            name, value = match[1], match[2] or ""
            in_repos = name == "repos" and not value
            if value == "|":
                block = []
                while index < len(lines) and (not lines[index].strip() or lines[index].startswith(" ")):
                    block.append(lines[index])
                    index += 1
                while block and not block[-1].strip():
                    block.pop()
                indent = min(len(text) - len(text.lstrip()) for text in block)
                config[name] = "\n".join(text[indent:] for text in block) + "\n"
            elif not in_repos:
                config[name] = yaml_scalar(value, where)
            continue
        if not in_repos:
            raise AssertionError(f"{where}: unrecognised line {line!r}")
        if match := re.fullmatch(r"  - repo: (\S+)", line):
            repo, hook = {"repo": match[1], "rev": None, "frozen": None, "hooks": []}, None
            repos.append(repo)
        elif repo and (match := re.fullmatch(r"    rev: (\S+)(?:\s+#\s*frozen:\s*(\S+))?", line)):
            repo["rev"], repo["frozen"] = match[1], match[2]
        elif repo and line == "    hooks:":
            pass
        elif repo and (match := re.fullmatch(r"      - id: (\S+)", line)):
            hook, key = {"id": match[1]}, None
            repo["hooks"].append(hook)
        elif hook and (match := re.fullmatch(r"        ([a-z_]+):(?: (.*))?", line)):
            key = match[1]
            hook[key] = yaml_scalar(match[2], where) if match[2] else []
        elif hook and key and isinstance(hook[key], list) and (match := re.fullmatch(r"          - (\S+)", line)):
            hook[key].append(match[1])
        else:
            raise AssertionError(f"{where}: unrecognised line {line!r}")
    return config, repos


def hooks_by_id(repos):
    hooks = {}
    for repo in repos:
        for hook in repo["hooks"]:
            if hook["id"] in hooks:
                raise AssertionError(f"hook {hook['id']} is configured twice")
            hooks[hook["id"]] = hook
    return hooks


def global_exclude():
    config, _ = read_pre_commit_config()
    return re.compile(config["exclude"])


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def mapping(lines, indent, where):
    """{key: (value, child lines)} for the `key: value` lines at exactly `indent` spaces in a block of YAML lines."""
    result, children = {}, None
    for line in lines:
        if not line.strip():
            if children is not None:
                children.append(line)
            continue
        if indent_of(line) > indent and children is not None:
            children.append(line)
            continue
        if line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(" " * indent + r"([A-Za-z_][\w-]*):(?: +(.*))?", line)
        if not match:
            raise AssertionError(f"{where}: unrecognised line {line!r}")
        if match[1] in result:
            raise AssertionError(f"{where}: {match[1]} appears twice")
        children = []
        result[match[1]] = (match[2] or "", children)
    for _, block in result.values():
        while block and not block[-1].strip():
            block.pop()
    return result


def block_text(value, children):
    """The text of a `key: |` literal block, of a one-paragraph `key: >-` folded block or of a one-line scalar."""
    if value == ">-":
        if any(not line.strip() for line in children):
            raise AssertionError("folded blocks of more than one paragraph are not supported here")
        return " ".join(line.strip() for line in children)
    if value != "|":
        return yaml_scalar(value, "scalar")
    indent = min(indent_of(line) for line in children if line.strip())
    return "\n".join(line[indent:] for line in children) + "\n"


def read_workflow(name):
    """The top-level keys of a workflow, and its jobs with their keys and steps."""
    path = WORKFLOWS / name
    top = mapping(path.read_text(encoding="utf-8").splitlines(), 0, name)
    jobs = {}
    for job_id, (_, job_lines) in mapping(top["jobs"][1], 2, f"{name} jobs").items():
        keys = mapping(job_lines, 4, f"{name} {job_id}")
        steps, current = [], None
        for line in keys.get("steps", ("", []))[1]:
            if line.startswith("      - "):
                current = ["        " + line[8:]]
                steps.append(current)
            elif current is not None:
                current.append(line)
            elif line.strip() and not line.lstrip().startswith("#"):
                raise AssertionError(f"{name} {job_id}: unrecognised step line {line!r}")
        parsed = []
        for step in steps:
            step_keys = mapping(step, 8, f"{name} {job_id} step")
            parsed.append(
                {
                    "name": yaml_scalar(step_keys["name"][0], name) if "name" in step_keys else None,
                    "keys": {key: value for key, (value, _) in step_keys.items()},
                    "run": block_text(*step_keys["run"]) if "run" in step_keys else None,
                    "env": {
                        key: value for key, (value, _) in mapping(step_keys.get("env", ("", []))[1], 10, name).items()
                    },
                    "lines": step,
                }
            )
        jobs[job_id] = {"keys": keys, "steps": parsed, "lines": job_lines}
    return top, jobs


def job_permissions(job):
    value, children = job["keys"].get("permissions", ("", []))
    return value or {key: child for key, (child, _) in mapping(children, 6, "permissions").items()}


def expand_braces(pattern):
    match = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return [expanded for option in match[1].split(",") for expanded in expand_braces(head + option + tail)]


def editorconfig_regex(pattern):
    """EditorConfig glob semantics: a pattern with a slash is relative to the root, one without matches any depth."""
    anchored = "/" in pattern
    parts, index = [], 0
    pattern = pattern.lstrip("/")
    while index < len(pattern):
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] in "*?":
            parts.append("[^/]*" if pattern[index] == "*" else "[^/]")
            index += 1
        elif pattern[index] in "[]{}!":
            raise AssertionError(f".editorconfig pattern {pattern!r} uses syntax these tests do not model")
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    body = "".join(parts)
    return re.compile(body if anchored else f"(?:.*/)?{body}")


def read_editorconfig():
    """The preamble and the (patterns, properties) sections of .editorconfig, in file order."""
    preamble, sections, current = {}, [], None
    for number, line in enumerate((ROOT / ".editorconfig").read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if match := re.fullmatch(r"\[(.+)\]", stripped):
            current = {}
            sections.append(([editorconfig_regex(p) for p in expand_braces(match[1])], current, match[1]))
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            raise AssertionError(f".editorconfig:{number}: unrecognised line {line!r}")
        (preamble if current is None else current)[key.strip().lower()] = value.strip()
    return preamble, sections


def editorconfig_properties(sections, path):
    properties = {}
    for patterns, values, _ in sections:
        if any(pattern.fullmatch(path) for pattern in patterns):
            properties.update(values)
    return properties


def default_properties(sections):
    return next(values for _, values, header in sections if header == "*")


def satisfies(version, spec):
    """Whether `node --version` output (vX.Y.Z) satisfies an npm range of ^X.Y.Z, >=X[.Y[.Z]] and <X[.Y[.Z]] terms."""
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return False
    have = tuple(int(part) for part in match.groups())
    for alternative in spec.split("||"):
        holds = True
        for comparator in alternative.split():
            term = re.fullmatch(r"(\^|>=|<)(\d+)(?:\.(\d+))?(?:\.(\d+))?", comparator)
            if not term or (term[1] == "^" and term[2] == "0"):
                raise AssertionError(f"engines comparator {comparator!r} is not modelled here")
            want = tuple(int(part or 0) for part in term.groups()[1:])
            if term[1] == "^":
                holds &= want <= have < (want[0] + 1, 0, 0)
            elif term[1] == ">=":
                holds &= have >= want
            else:
                holds &= have < want
        if holds:
            return True
    return False


class PinTests(unittest.TestCase):
    def test_lint_requirements_are_exact_pins(self):
        pins = read_requirements("requirements-lint.txt")
        self.assertLessEqual({"coverage", "mypy", "pre-commit", "ruff", "types-requests"}, set(pins))
        # coverage.py reads [tool.coverage] from pyproject.toml through tomli on Python 3.10.
        self.assertIn("toml", pins["coverage"][0])
        self.assertEqual([name for name, (extras, _) in pins.items() if extras and name != "coverage"], [])

    def test_hook_versions_match_the_pip_pins(self):
        config, repos = read_pre_commit_config()
        pins = read_requirements("requirements-lint.txt")
        frozen = {repo["repo"]: repo["frozen"] for repo in repos}
        self.assertEqual(frozen["https://github.com/astral-sh/ruff-pre-commit"], "v" + pins["ruff"][1])
        self.assertEqual(frozen["https://github.com/pre-commit/mirrors-mypy"], "v" + pins["mypy"][1])
        self.assertLessEqual(version_tuple(config["minimum_pre_commit_version"]), version_tuple(pins["pre-commit"][1]))

    def test_remote_hooks_are_frozen_to_full_commits(self):
        _, repos = read_pre_commit_config()
        remote = [repo for repo in repos if repo["repo"] not in ("local", "meta")]
        self.assertGreaterEqual(len(remote), 4)
        for repo in remote:
            with self.subTest(repo=repo["repo"]):
                self.assertTrue(repo["repo"].startswith("https://github.com/"))
                self.assertIsNotNone(FULL_SHA.fullmatch(repo["rev"] or ""), "rev must be a full commit SHA")
                self.assertRegex(repo["frozen"] or "", r"^v\d+\.\d+\.\d+$", "rev needs a `# frozen: vX.Y.Z` comment")

    def test_mypy_sees_the_pinned_runtime_dependencies(self):
        _, repos = read_pre_commit_config()
        mypy = hooks_by_id(repos)["mypy"]
        runtime = read_requirements("requirements.txt")
        lint = read_requirements("requirements-lint.txt")
        expected = {name: version for name, (_, version) in runtime.items()}
        expected["types-requests"] = lint["types-requests"][1]
        self.assertLessEqual({"pillow", "requests", "beautifulsoup4"}, set(runtime))
        declared = {}
        for requirement in mypy["additional_dependencies"]:
            match = REQUIREMENT.fullmatch(requirement)
            self.assertIsNotNone(match, f"{requirement} is not an exact pin")
            declared[normalize(match["name"])] = match["version"]
        self.assertEqual(declared, expected)
        # mypy checks the [tool.mypy] files, not the staged ones, and without the mirror's default
        # --ignore-missing-imports, which would hide a missing dependency.
        self.assertEqual(mypy["pass_filenames"], "false")
        self.assertEqual(mypy["args"], [])

    def test_npm_packages_are_exact_and_locked(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertIs(package.get("private"), True)
        self.assertTrue(package["devDependencies"])
        for name, version in package["devDependencies"].items():
            with self.subTest(package=name):
                self.assertRegex(version, r"^\d+\.\d+\.\d+$", "devDependencies need exact versions")
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            self.assertNotIn(field, package, "only the pinned lint tools are installed")
        self.assertEqual(lock["lockfileVersion"], 3)
        root = lock["packages"][""]
        self.assertEqual(root.get("devDependencies"), package["devDependencies"])
        self.assertFalse(root.get("dependencies"))
        self.assertEqual(root.get("engines"), package["engines"])
        for name, version in package["devDependencies"].items():
            with self.subTest(locked=name):
                self.assertEqual(lock["packages"].get(f"node_modules/{name}", {}).get("version"), version)
        packages = [path for path in lock["packages"] if path]
        self.assertGreater(len(packages), len(package["devDependencies"]))
        for path in packages:
            with self.subTest(lockfile=path):
                entry = lock["packages"][path]
                self.assertRegex(entry.get("integrity", ""), r"^sha512-[A-Za-z0-9+/]+={0,2}$")
                self.assertTrue(entry.get("resolved", "").startswith("https://registry.npmjs.org/"))


class PreCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.repos = read_pre_commit_config()
        cls.hooks = hooks_by_id(cls.repos)
        cls.text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    def test_no_hook_fixes_unasked(self):
        # mixed-line-ending defaults to --fix=auto; --fix=no only reports.
        self.assertEqual(self.hooks["mixed-line-ending"]["args"], ["--fix=no"])
        self.assertIsNone(re.search(r"--fix\b(?!=no\b)|--write\b|--unsafe-fixes\b", self.text))
        self.assertEqual(FORBIDDEN_HOOKS & set(self.hooks), set())
        for hook in FORBIDDEN_HOOKS:
            self.assertNotIn(hook, self.text)

    def test_ruff_format_hook_receives_python_files_only(self):
        # The hook's default types_or includes markdown; ruff would reformat code blocks in the documents.
        self.assertEqual(self.hooks["ruff-format"]["types_or"], ["python", "pyi"])

    def test_eslint_only_checks_src(self):
        eslint = self.hooks["eslint"]
        repo = next(repo for repo in self.repos if eslint in repo["hooks"])
        self.assertEqual(repo["repo"], "local")
        self.assertEqual(eslint["language"], "system")
        self.assertTrue(eslint["entry"].startswith("npx --no-install eslint "), "use the npm ci install, no download")
        self.assertIn("--max-warnings=0", eslint["entry"].split())
        self.assertNotIn("--fix", " ".join([eslint["entry"], *eslint.get("args", [])]))
        files = re.compile(eslint["files"])
        for path in ("src/archive.js", "src/i18n/common.js", "src/archive.css", "src/slideshow.css"):
            self.assertRegex(path, files)
        for path in ("templates/slideshow.html", "archive.html", "scripts/build_catalog.py", "eslint.config.mjs"):
            self.assertNotRegex(path, files)
        self.assertRegex("src/vendor/legacy-ui.js", global_exclude())

    def test_global_exclude_skips_pinned_and_generated_files(self):
        exclude = global_exclude()
        for path in (*PINNED_SAMPLES, *GENERATED, NEW_THUMB):
            with self.subTest(path=path):
                self.assertRegex(path, exclude)
        for path in SOURCES:
            with self.subTest(source=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertNotRegex(path, exclude)
        for path in LOOKALIKES:
            with self.subTest(lookalike=path):
                self.assertNotRegex(path, exclude)


@unittest.skipIf(tomllib is None, "reading pyproject.toml needs tomllib (Python 3.11 or newer); CI runs Python 3.12")
class PyprojectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = read_pyproject()

    def test_ruff_settings(self):
        ruff = self.tool["ruff"]
        self.assertEqual(ruff["line-length"], 120)
        self.assertEqual(ruff["target-version"], "py310")
        self.assertIs(ruff["force-exclude"], True)
        # ruff 0.17 would otherwise also reformat Python code blocks in the hand-edited, published Markdown.
        self.assertEqual(ruff["include"], ["*.py", "*.pyi", "**/pyproject.toml"])
        self.assertLessEqual({"assets", "data", "scripts/legacy", "src/vendor"}, set(ruff["extend-exclude"]))
        self.assertEqual(ruff["lint"]["select"], ["E4", "E7", "E9", "F", "W", "I", "B", "UP", "PLE"])
        self.assertIs(ruff.get("fix", False), False)
        self.assertIs(ruff.get("unsafe-fixes", False), False)

    def test_mypy_checks_the_build_and_publication_scripts(self):
        mypy = self.tool["mypy"]
        self.assertEqual(mypy["python_version"], "3.10")
        self.assertEqual(self.tool["ruff"]["target-version"], "py" + mypy["python_version"].replace(".", ""))
        self.assertEqual(mypy["files"], MYPY_FILES)
        self.assertIs(mypy.get("strict", False), False, "stage 1 is not strict")
        exclude = global_exclude()
        for path in mypy["files"]:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertNotRegex(path, exclude)

    def test_coverage_measures_scripts_and_their_subprocesses(self):
        run, report = self.tool["coverage"]["run"], self.tool["coverage"]["report"]
        self.assertEqual(run["source"], ["scripts"])
        legacy = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "scripts/legacy").glob("*.py"))
        self.assertTrue(legacy)
        for path in legacy:
            self.assertTrue(any(fnmatch.fnmatch(path, pattern) for pattern in run["omit"]), f"{path} is measured")
        for path in MYPY_FILES:
            self.assertFalse(any(fnmatch.fnmatch(path, pattern) for pattern in run["omit"]), f"{path} is omitted")
        self.assertIn("subprocess", run["patch"])
        self.assertIs(run["parallel"], True)
        self.assertIs(type(report["fail_under"]), int)
        self.assertGreater(report["fail_under"], 0)
        self.assertLessEqual(report["fail_under"], 100)


class LinterConfigTests(unittest.TestCase):
    def test_eslint_config_checks_classic_scripts_without_fixing(self):
        text = (ROOT / "eslint.config.mjs").read_text(encoding="utf-8")
        # Comment lines only: the globs ("src/**") look like block comments to a regular expression.
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
        self.assertRegex(code, r'\{\s*ignores:\s*\[\s*"src/vendor/\*\*"\s*\]\s*,?\s*\}')
        self.assertEqual(re.findall(r"sourceType:\s*\"(\w+)\"", code), ["script"])
        self.assertIsNone(re.search(r"\bfix\b", code))
        self.assertNotIn("--fix", (ROOT / "package.json").read_text(encoding="utf-8"))

    def test_editorconfig_leaves_pinned_and_generated_files_alone(self):
        preamble, sections = read_editorconfig()
        self.assertEqual(preamble, {"root": "true"})
        defaults = default_properties(sections)
        self.assertLessEqual(
            {"charset", "end_of_line", "insert_final_newline", "trim_trailing_whitespace"}, set(defaults)
        )
        for path in (*PINNED_SAMPLES, *GENERATED, NEW_THUMB):
            with self.subTest(path=path):
                properties = editorconfig_properties(sections, path)
                self.assertEqual({key: properties[key] for key in defaults}, dict.fromkeys(defaults, "unset"))
        for path in SOURCES:
            with self.subTest(source=path):
                properties = editorconfig_properties(sections, path)
                self.assertEqual(properties["end_of_line"], "lf")
                self.assertEqual(properties["trim_trailing_whitespace"], "true")

    def test_editorconfig_keeps_the_byte_order_marks(self):
        _, sections = read_editorconfig()
        for path in BOM_FILES:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).read_bytes().startswith(BOM))
                self.assertEqual(editorconfig_properties(sections, path)["charset"], "utf-8-bom")
        for path in SOURCES:
            if path not in BOM_FILES:
                self.assertEqual(editorconfig_properties(sections, path)["charset"], "utf-8")


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {path.name: read_workflow(path.name) for path in sorted(WORKFLOWS.glob("*.yml"))}
        cls.top, cls.jobs = cls.workflows["tests.yml"]

    def step(self, job, needle):
        steps = [step for step in self.jobs[job]["steps"] if step["run"] and needle in step["run"]]
        self.assertEqual(len(steps), 1, f"expected one {job} step running {needle!r}")
        return steps[0]

    def test_every_action_is_pinned_to_one_full_commit(self):
        pins = {}
        for name in self.workflows:
            lines = (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
            uses = [match[1] for line in lines if (match := re.fullmatch(r"\s*(?:- )?uses:\s*(.*?)\s*", line))]
            self.assertTrue(uses, name)
            for value in uses:
                with self.subTest(workflow=name, uses=value):
                    if LOCAL_WORKFLOW.fullmatch(value):
                        self.assertTrue((ROOT / value).is_file())
                        continue
                    match = PINNED_ACTION.fullmatch(value)
                    self.assertIsNotNone(match, "pin actions as owner/name@<full commit SHA> # vX.Y.Z")
                    pins.setdefault(match["action"], set()).add((match["sha"], match["tag"]))
        self.assertIn("actions/checkout", pins)
        self.assertEqual({action: len(found) for action, found in pins.items() if len(found) > 1}, {})

    def test_run_scripts_take_expressions_from_env_and_never_swallow_errors(self):
        for name, (_, jobs) in self.workflows.items():
            for job_id, job in jobs.items():
                for step in job["steps"]:
                    with self.subTest(workflow=name, job=job_id, step=step["name"]):
                        if step["keys"].get("uses", "").startswith("actions/checkout@"):
                            self.assertIn("          persist-credentials: false", step["lines"])
                        if step["run"] is not None:
                            self.assertNotIn("${{", step["run"])
                            self.assertIsNone(re.search(r"\|\|\s*(?:true|:)\s*(?:$|[;)])", step["run"], re.MULTILINE))

    def test_tests_workflow_is_read_only_and_callable(self):
        triggers = mapping(self.top["on"][1], 2, "on")
        self.assertLessEqual({"push", "pull_request", "workflow_call"}, set(triggers))
        # Every push and every pull request, whatever the branch: no filters.
        self.assertEqual(triggers["push"], ("", []))
        self.assertEqual(triggers["pull_request"], ("", []))
        self.assertEqual(self.top["permissions"][1], ["  contents: read"])
        lines = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8").splitlines()
        self.assertEqual([line for line in lines if GRANTS_WRITE.fullmatch(line) or "write-all" in line], [])
        for job_id, job in self.jobs.items():
            with self.subTest(job=job_id):
                self.assertIn(job_permissions(job), ({}, {"contents": "read"}))

    def test_lint_job_runs_every_hook_and_leaves_the_checkout_unchanged(self):
        lint = self.jobs["lint"]
        self.assertEqual(yaml_scalar(lint["keys"]["name"][0], "name"), "Lint, format and type checks")
        self.assertEqual(job_permissions(lint), {"contents": "read"})
        install = self.step("lint", "requirements-lint.txt")
        self.assertEqual(install["keys"].get("id"), "install")
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund\n", install["run"])
        hooks = self.step("lint", "-m pre_commit")
        self.assertIn(
            '"$RUNNER_TEMP/venv/bin/python" -I -m pre_commit run --all-files --show-diff-on-failure', hooks["run"]
        )
        self.assertEqual(hooks["keys"]["if"], "${{ !cancelled() && steps.install.outcome == 'success' }}")
        self.assertTrue(
            hooks["env"]["PRE_COMMIT_HOME"].startswith("${{ runner.temp }}/"), "keep hook caches out of the checkout"
        )
        unchanged = self.step("lint", "git status --porcelain --untracked-files=all")
        self.assertEqual(unchanged["keys"]["if"], "${{ !cancelled() }}")
        steps = lint["steps"]
        self.assertLess(steps.index(install), steps.index(hooks))
        self.assertLess(steps.index(hooks), steps.index(unchanged))

    def test_lint_job_node_check_matches_package_engines(self):
        if not shutil.which("bash"):
            self.skipTest("needs bash to run the workflow's Node.js version check")
        engines = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
        script = self.step("lint", "node --version")["run"]
        self.assertIn(engines, script, "the error message names the engines range")
        lines = script.splitlines()
        self.assertIn('node_version="$(node --version)"', lines, "the check below runs that version test as written")
        start = lines.index('node_version="$(node --version)"')
        end = next(index for index, line in enumerate(lines) if line.startswith("npm ci "))
        check = "\n".join(['node_version="$1"', *lines[start + 1 : end]])
        probes = [
            f"v{major}.{minor}.{patch}"
            for major in range(18, 27)
            for minor in (0, 12, 13, 18, 19, 20)
            for patch in (0, 3)
        ]
        probes += ["v24.0.0-rc.1", "24.0.0", "v24.0", "", "vX.Y.Z"]
        for version in probes:
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", check, "bash", version],
                capture_output=True,
                text=True,
            )
            with self.subTest(node=version):
                self.assertEqual(result.returncode == 0, satisfies(version, engines), result.stdout)
                self.assertEqual("::error::" in result.stdout, result.returncode != 0)

    def test_unit_tests_run_under_coverage_and_the_report_enforces_fail_under(self):
        tests = self.step("checks", "for test in tests/test_*.py")
        self.assertIn('"$RUNNER_TEMP/venv/bin/python" -I -m coverage run "$test" || failed+=("$test")', tests["run"])
        self.assertIn('echo "::error file=$test::$test failed"', tests["run"])
        report = self.step("checks", "-m coverage report")
        self.assertEqual(tests["env"], report["env"])
        data_file = tests["env"]["COVERAGE_FILE"]
        self.assertTrue(data_file.startswith("${{ runner.temp }}/") and data_file.endswith("/.coverage"))
        directory = data_file.replace("${{ runner.temp }}", "$RUNNER_TEMP").removesuffix("/.coverage")
        self.assertIn(f'mkdir -p "{directory}"', tests["run"])
        self.assertIn(f'-I -m coverage combine --quiet "{directory}"', report["run"])
        commands = [line for line in report["run"].splitlines() if "-m coverage report" in line]
        self.assertIn('"$RUNNER_TEMP/venv/bin/python" -I -m coverage report', [line.strip() for line in commands])
        for line in commands:
            if "--fail-under" in line:
                self.assertIn("--format=markdown --fail-under=0", line, "only the summary table ignores fail_under")
        self.assertIn('} >> "$GITHUB_STEP_SUMMARY"', report["run"])
        install = self.step("checks", "pip install")
        self.assertIn("-r requirements-lint.txt", install["run"])
        steps = self.jobs["checks"]["steps"]
        self.assertEqual(steps.index(report), steps.index(tests) + 1)
        for step in steps[steps.index(tests) :]:
            with self.subTest(step=step["name"]):
                self.assertEqual(step["keys"].get("if"), "${{ !cancelled() && steps.install.outcome == 'success' }}")

    def test_pages_deploys_only_after_the_tests_workflow(self):
        # guard -> test (all of tests.yml) -> build (the artifact) -> deploy. None of the jobs up to the deploy job has
        # an if: that could run it after an earlier one failed: a job runs only when every job it needs succeeded,
        # unless its condition calls a status function (the deploy job's condition, pinned in tests/test_pages.py, calls
        # none). The re-run check job comes after them all; its condition calls cancelled(), a status function, and
        # negates it, but it has no permissions and only fails the run (pinned in tests/test_pages.py).
        _, jobs = self.workflows["pages.yml"]

        def needs(job_id):
            value = yaml_scalar(jobs[job_id]["keys"].get("needs", ("",))[0], "needs")
            return set(value) if isinstance(value, list) else {value} - {""}

        def uses(job):
            return {step["keys"].get("uses", "").split("@")[0] for step in job["steps"]} - {""}

        callers = [
            job_id
            for job_id, job in jobs.items()
            if job["keys"].get("uses", ("",))[0] == "./.github/workflows/tests.yml"
        ]
        self.assertEqual(callers, ["test"])
        self.assertEqual(set(jobs["test"]["keys"]), {"name", "needs", "uses", "permissions"}, "no inputs, no if:")
        builders = [job_id for job_id, job in jobs.items() if "actions/upload-pages-artifact" in uses(job)]
        deployers = [job_id for job_id, job in jobs.items() if "actions/deploy-pages" in uses(job)]
        self.assertEqual((builders, deployers), (["build"], ["deploy"]))
        self.assertEqual(needs("guard"), set())
        self.assertEqual(needs("test"), {"guard"})
        self.assertEqual(needs("build"), {"guard", "test"})
        self.assertEqual(needs("deploy"), {"guard", "test", "build"})
        self.assertEqual(needs("rerun-check"), {"guard", "test", "build", "deploy"})
        for job_id in ("guard", "test", "build"):
            self.assertNotIn("if", jobs[job_id]["keys"], job_id)
        self.assertNotRegex(
            block_text(*jobs["deploy"]["keys"]["if"]), r"\b(?:always|success|failure|cancelled)\s*\(", "status check"
        )
        text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error", text)

    def test_pages_permissions_are_minimal(self):
        # Nothing by default; read access for the jobs that check out and test the release; the Pages and OIDC grants
        # only for the deploy job, which runs no repository code: it has no checkout and no run step.
        top, jobs = self.workflows["pages.yml"]
        self.assertEqual(top["permissions"], ("{}", []))
        self.assertEqual(
            {job_id: job_permissions(job) for job_id, job in jobs.items()},
            {
                "guard": {"contents": "read"},
                "test": {"contents": "read"},
                "build": {"contents": "read"},
                "deploy": {"pages": "write", "id-token": "write"},
                "rerun-check": "{}",
            },
        )
        lines = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8").splitlines()
        grants = [line.strip() for line in lines if GRANTS_WRITE.fullmatch(line) or "write-all" in line]
        self.assertEqual(grants, ["pages: write", "id-token: write"])
        deploy = jobs["deploy"]
        self.assertEqual([step["run"] for step in deploy["steps"]], [None, None])
        self.assertEqual(
            [step["keys"]["uses"].split("@")[0] for step in deploy["steps"]],
            ["actions/configure-pages", "actions/deploy-pages"],
        )
        actions = {match[1] for line in lines if (match := re.fullmatch(r"\s*(?:- )?uses: ([^@\s]+)(?:@.*)?", line))}
        self.assertEqual(
            actions,
            {
                "actions/checkout",
                "./.github/workflows/tests.yml",
                "actions/upload-pages-artifact",
                "actions/configure-pages",
                "actions/deploy-pages",
            },
        )


@unittest.skipUnless(in_git_checkout(), "needs git and a Git checkout of this repository")
class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tracked = git("ls-files", "-z").decode("utf-8").split("\0")[:-1]
        fields = git("check-attr", "-z", "--stdin", "text", stdin="\0".join(cls.tracked).encode()).split(b"\0")[:-1]
        if len(fields) != 3 * len(cls.tracked):
            raise AssertionError("git check-attr did not report every tracked path")
        cls.byte_pinned = sorted(
            path.decode("utf-8")
            for path, _, value in zip(fields[::3], fields[1::3], fields[2::3], strict=True)
            if value == b"unset"
        )

    def test_byte_pinned_paths_cover_every_kind(self):
        for prefix in ("LICENSE", "licenses/", "src/vendor/", "scripts/legacy/", "assets/"):
            with self.subTest(prefix=prefix):
                self.assertTrue(any(path.startswith(prefix) for path in self.byte_pinned))

    def test_global_exclude_skips_every_byte_pinned_and_generated_file(self):
        exclude = global_exclude()
        self.assertLessEqual(set(GENERATED), set(self.tracked))
        paths = [*self.byte_pinned, *GENERATED, NEW_THUMB]
        self.assertEqual([path for path in paths if not exclude.search(path)], [])

    def test_global_exclude_keeps_every_maintained_file(self):
        exclude = global_exclude()
        skipped = set(self.byte_pinned) | set(GENERATED)
        maintained = [
            path
            for path in self.tracked
            if (path.startswith(LINTED_TREES) and not path.startswith(UNLINTED_TREES))
            or ("/" not in path and path not in skipped)
        ]
        self.assertGreater(len(maintained), 50)
        self.assertEqual([path for path in maintained if exclude.search(path)], [])

    @unittest.skipIf(tomllib is None, "reading pyproject.toml needs tomllib (Python 3.11 or newer)")
    def test_ruff_excludes_every_byte_pinned_python_file(self):
        excluded = read_pyproject()["ruff"]["extend-exclude"]
        python = [path for path in self.byte_pinned if path.endswith(".py")]
        self.assertTrue(python)
        for path in python:
            with self.subTest(path=path):
                self.assertTrue(any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in excluded))

    def test_gitignore_covers_tool_output_and_no_tracked_file(self):
        def ignored(path):
            result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", "-q", "--", path])
            self.assertIn(result.returncode, (0, 1), f"git check-ignore failed for {path}")
            return result.returncode == 0

        for path in (
            ".coverage",
            ".coverage.runner.1234.XyZ",
            "tests/.coverage",
            "node_modules/eslint/package.json",
            ".mypy_cache/3.12/cache.db",
            ".ruff_cache/CACHEDIR.TAG",
        ):
            with self.subTest(ignored=path):
                self.assertTrue(ignored(path))
        for path in SOURCES:
            with self.subTest(kept=path):
                self.assertFalse(ignored(path))
        self.assertEqual(git("ls-files", "-ci", "--exclude-standard").decode(), "")

    def test_editorconfig_leaves_every_byte_pinned_file_alone(self):
        _, sections = read_editorconfig()
        defaults = default_properties(sections)
        changed = [
            path
            for path in self.byte_pinned
            if any(editorconfig_properties(sections, path).get(key) != "unset" for key in defaults)
        ]
        self.assertEqual(changed, [])

    def test_editorconfig_charset_matches_every_byte_order_mark(self):
        _, sections = read_editorconfig()
        mismatched = []
        for path in self.tracked:
            charset = editorconfig_properties(sections, path).get("charset")
            if charset == "unset":
                continue
            with (ROOT / path).open("rb") as file:
                has_bom = file.read(len(BOM)) == BOM
            if has_bom != (charset == "utf-8-bom"):
                mismatched.append(path)
        self.assertEqual(mismatched, [])
        self.assertTrue(all(editorconfig_properties(sections, path)["charset"] == "utf-8-bom" for path in BOM_FILES))


if __name__ == "__main__":
    unittest.main()
