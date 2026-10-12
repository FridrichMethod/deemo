"""Ensure only a release is deployed, and only tracked, allowlisted website files reach the GitHub Pages payload.

tests/test_pages_guards.py runs the guard steps whose names and order these tests pin.
"""

import importlib.util
import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pages = load("prepare_pages", "scripts/prepare_pages.py")
# The workflow reader of the tooling tests: the standard library has no YAML parser, and that reader follows the block
# layout of these workflows line by line and fails on a line it does not recognise.
workflows = load("test_tooling", "tests/test_tooling.py")

# The deploy job's condition, verbatim. The expression language has no regular expressions, so this only requires a tag
# ref that starts with v, from a published full release or a manual run; the guard steps check the whole vX.Y.Z tag.
# Its == coerces null and false both to 0, so `prerelease == false` would also accept a release event without the flag;
# toJSON() turns false into 'false' and a missing flag into 'null'. The guard job's outputs are the commit its steps
# approved and the attempt of the run they ran in, both empty unless every one of them passed. A re-run of the deploy job
# alone, or of later jobs only, keeps the guard job's outputs of the attempt it repeats and looks up no Release, so the
# attempt must be the run's own (RE_RUNS).
DEPLOY_CONDITION = (
    "startsWith(github.ref, 'refs/tags/v') && needs.guard.outputs.commit == github.sha && "
    "needs.guard.outputs.attempt == github.run_attempt && "
    "(github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'release' && github.event.action == 'published' && "
    "toJSON(github.event.release.prerelease) == 'false'))"
)
# The part of GitHub's expression language that the deploy condition uses: string, true, false and null literals,
# context properties (null when absent), ==, && and || (each gives one of its operands), parentheses, startsWith() and
# toJSON(). == compares two strings ignoring case and two values of one other type as they are; values of two types
# become numbers first (null and false 0, true 1, '' 0, another string its JSON number or NaN, which equals nothing).
# startsWith() casts both arguments to strings (null to '') and ignores case. Any other syntax is an error.
EXPRESSION_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(==|&&|\|\||[(),])|([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*))")
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
LITERALS = {"true": True, "false": False, "null": None}


def as_number(value):
    if isinstance(value, str):
        return 0.0 if value == "" else float(value) if JSON_NUMBER.fullmatch(value) else math.nan
    return float(bool(value))


def as_text(value):
    return "" if value is None else str(value).lower() if isinstance(value, bool) else value


def truthy(value):
    return value is True or (isinstance(value, str) and value != "")


def equal(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right if type(left) is type(right) else as_number(left) == as_number(right)


FUNCTIONS = {
    "startsWith": (2, lambda text, prefix: as_text(text).casefold().startswith(as_text(prefix).casefold())),
    "toJSON": (1, lambda value: json.dumps(value, ensure_ascii=False)),
}


def tokenize(expression):
    """(string, operator, name) for each token of expression, with only one of the three set, and an end marker."""
    tokens, position = [], 0
    while expression[position:].strip():
        match = EXPRESSION_TOKEN.match(expression, position)
        if match is None:
            raise ValueError(f"unsupported expression syntax: {expression[position:]!r}")
        tokens.append(match.groups())
        position = match.end()
    return [*tokens, (None, None, None)]


class Evaluation:
    """An expression evaluated with contexts. Each method reads the tokens from index on and gives the value of what it
    read and the index after it: either() an || chain, both() an && chain, comparison() an == or one operand."""

    def __init__(self, expression, contexts):
        self.expression, self.contexts, self.tokens = expression, contexts, tokenize(expression)

    def expect(self, index, operator):
        if self.tokens[index][1] != operator:
            raise ValueError(f"expected {operator!r} in {self.expression!r}")
        return index + 1

    def operand(self, index):
        string, operator, name = self.tokens[index]
        if operator == "(":
            value, index = self.either(index + 1)
            return value, self.expect(index, ")")
        if string is not None:
            return string[1:-1].replace("''", "'"), index + 1
        if name is None:
            raise ValueError(f"expected an operand in {self.expression!r}")
        if name in LITERALS:
            return LITERALS[name], index + 1
        if self.tokens[index + 1][1] == "(":
            return self.call(name, index + 1)
        return self.contexts.get(name), index + 1

    def call(self, name, index):
        if name not in FUNCTIONS:
            raise ValueError(f"unsupported function {name}()")
        arity, function = FUNCTIONS[name]
        arguments = []
        while not arguments or self.tokens[index][1] == ",":
            value, index = self.either(index + 1)
            arguments.append(value)
        if len(arguments) != arity:
            raise ValueError(f"{name}() takes {arity} arguments")
        return function(*arguments), self.expect(index, ")")

    def comparison(self, index):
        value, index = self.operand(index)
        if self.tokens[index][1] == "==":
            right, index = self.operand(index + 1)
            value = equal(value, right)
        return value, index

    def both(self, index):
        value, index = self.comparison(index)
        while self.tokens[index][1] == "&&":
            right, index = self.comparison(index + 1)
            value = right if truthy(value) else value
        return value, index

    def either(self, index):
        value, index = self.both(index)
        while self.tokens[index][1] == "||":
            right, index = self.both(index + 1)
            value = value if truthy(value) else right
        return value, index


def evaluate(expression, contexts):
    """The value of expression, with contexts mapping property paths such as github.ref to their values."""
    evaluation = Evaluation(expression, contexts)
    value, index = evaluation.either(0)
    if index != len(evaluation.tokens) - 1:
        raise ValueError(f"unexpected {evaluation.tokens[index]} in {expression!r}")
    return value


# A release commit, and the deploy job's contexts in the attempt run_attempt of a run on its tag v1.1.2 that event
# started, with the guard job's outputs as the deploy job sees them.
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def deployment(run_attempt, event="release", **outputs):
    contexts = {"github.event_name": event, "github.ref": "refs/tags/v1.1.2", "github.sha": COMMIT}
    if event == "release":
        contexts.update({"github.event.action": "published", "github.event.release.prerelease": False})
    return {
        **contexts,
        "github.run_attempt": run_attempt,
        **{f"needs.guard.outputs.{name}": value for name, value in outputs.items()},
    }


# Attempts of a run whose jobs the deploy job needs all succeeded, and whether its condition then holds. A re-run of the
# whole run runs the guard job, and with it every guard step and the Release lookup, again in the new attempt. A re-run
# of the deploy job alone, or of the failed jobs after only a later job than the guard job failed, does not: the deploy
# job then sees the guard job's outputs of the attempt it repeats. A guard job that refused the run recorded nothing.
RE_RUNS = {
    "the first attempt of a release's run": (deployment("1", commit=COMMIT, attempt="1"), True),
    "the first attempt of a manual run": (deployment("1", "workflow_dispatch", commit=COMMIT, attempt="1"), True),
    "a re-run of the whole run": (deployment("2", commit=COMMIT, attempt="2"), True),
    "a re-run of the whole manual run": (deployment("2", "workflow_dispatch", commit=COMMIT, attempt="2"), True),
    "a third attempt, a re-run of the whole run": (deployment("3", commit=COMMIT, attempt="3"), True),
    "a re-run of the deploy job alone": (deployment("2", commit=COMMIT, attempt="1"), False),
    "a re-run of the deploy job alone, in a manual run": (
        deployment("2", "workflow_dispatch", commit=COMMIT, attempt="1"),
        False,
    ),
    "a re-run of the failed jobs after only the build job failed": (deployment("2", commit=COMMIT, attempt="1"), False),
    "a re-run of the deploy job alone after a re-run of the whole run": (
        deployment("3", commit=COMMIT, attempt="2"),
        False,
    ),
    "attempt 11 with the guard job's outputs of attempt 1": (deployment("11", commit=COMMIT, attempt="1"), False),
    "a guard job without the attempt output": (deployment("1", commit=COMMIT), False),
    "a guard job whose attempt output is empty": (deployment("1", commit=COMMIT, attempt=""), False),
    "a guard job without outputs": (deployment("1"), False),
    "a guard job whose outputs are empty": (deployment("1", commit="", attempt=""), False),
}
# The guard steps of both jobs by role, in the order they run. In tests/test_pages_guards.py its Checkout class stands
# in for the checkout step.
GUARD_STEPS = (
    ("event", "Refuse anything but a published release or a manual run on a vX.Y.Z tag"),
    ("lookup", "Look up the GitHub Release of the tag"),
    ("release", "Refuse a tag without a published Release that is not a prerelease"),
    ("checkout", "Checkout"),
    ("main", "Refuse a release commit that is not on main"),
)
# The guard job's last step, which gives the commit that every guard step passed as the job's output.
APPROVE = "Approve the release commit"
# A release tag as the guard steps accept it: three numbers without leading zeros.
VERSION = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
# "[vX.Y.Z](<its release page>) (YYYY-MM-DD)", with ASCII or full-width parentheses around the date.
CURRENT_RELEASE = re.compile(
    rf"\[({VERSION})\]\(https://github\.com/FridrichMethod/deemo/releases/tag/({VERSION})\) ?[(（]"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})[)）]"
)
DEPLOYMENT_SECTIONS = {"README.md": "## Website deployment", "README.zh-CN.md": "## 网站部署"}
# What the READMEs said while a push to main deployed the site.
PUSH_DEPLOYED = {
    "README.md": ("After a push to `main`", "deploys only from `main`", "Pages workflow runs every check again"),
    "README.zh-CN.md": ("推送到 `main` 后", "只从 `main` 部署", "Pages 工作流会再次运行全部检查"),
}
# What each README's deployment section says about the release rules of 2026-10-12: what every run checks, a re-run of
# a release's run included, that a re-run without the guard job (named as on the Actions page) deploys nothing, the
# tag form, and the two repository settings that limit which tags can deploy.
GUARD_JOB = "Release guard"
RELEASE_RULES = {
    "README.md": (
        "Every run, whether a release or a manual run started it, first checks",
        "one read-only request to the GitHub API",
        "A re-run keeps the event of the run it repeats",
        "also refuses a re-run of a release's run",
        f"A re-run that leaves out the {GUARD_JOB} job, such as a re-run of the deploy job alone, does not look the"
        " Release up again and deploys nothing, so re-run the whole run.",
        "a run on a tag without such a Release",
        "three numbers without leading zeros",
        "`v01.1.0`",
        'A repository ruleset ("Release tags v*"',
        "lets only repository admins create, move or delete `v*` tags",
        "the rules of the `github-pages` environment accept deployments only from tags from `v1.1.1` on",
    ),
    "README.zh-CN.md": (
        "每次运行，无论由发布版还是手动触发，都会先",
        "向 GitHub API 发出一次只读请求",
        "重新运行（re-run）沿用被重新运行的那次运行的事件",
        "也会被这项检查拒绝",
        f"不包括 {GUARD_JOB} 任务的重新运行（例如只重新运行部署任务）不会再次查询 Release，也不会部署任何内容，"
        "因此应重新运行整次运行。",
        "在没有这样的 Release 的标签上的运行",
        "三个不带前导零的数字",
        "`v01.1.0`",
        '仓库规则集（ruleset）"Release tags v*"',
        "只允许仓库管理员创建、移动或删除 `v*` 标签",
        "`github-pages` 环境的部署规则只接受 `v1.1.1` 及之后的标签",
    ),
}
# What the READMEs said while a manual run did not look up the tag's Release.
NO_RELEASE_LOOKUP = {
    "README.md": ("does not look up the tag's GitHub Release", "use it only to redeploy a released tag"),
    "README.zh-CN.md": ("不会查询该标签的 GitHub Release", "只用它重新部署已发布的标签"),
}
# The first tag whose pages.yml looks up the tag's Release on every run. A run takes pages.yml from the tag's commit, so
# a run on an older tag runs a workflow that does not, and an example of a manual run names no such tag.
FIRST_CHECKED_TAG = "v1.1.1"
MANUAL_RUN = re.compile(rf"gh workflow run pages\.yml --ref ({VERSION})")
# What each README's deployment section and the header comment of pages.yml say about a run on an older tag.
OLDER_TAGS = {
    "README.md": (
        "A run on a tag older than `v1.1.1` would use that tag's own workflow, which does not look up the Release",
        "the environment refuses its deployment",
    ),
    "README.zh-CN.md": (
        "在早于 `v1.1.1` 的标签上的运行会使用该标签自己的工作流，它不会查询 Release",
        "环境会拒绝它的部署",
    ),
    ".github/workflows/pages.yml": (
        "A run on a tag older than v1.1.1 would take that tag's own workflow, which does not look up the Release",
        "the environment refuses its deployment",
    ),
}
# The same rules in the header comment of pages.yml, whose lines are joined with spaces.
HEADER_RULES = (
    "a published GitHub Release, not marked as a prerelease",
    "Every run, whichever of the two started it, first asks for the tag's Release",
    "one read-only GitHub API request",
    "a re-run keeps the event of the run it repeats",
    "a re-run that leaves out the guard job, such as one of the deploy job alone, repeats no lookup and deploys nothing,"
    " so re-run the whole run",
    "the commit the guard job approved in the same attempt",
    "three numbers without leading zeros",
    'A repository ruleset ("Release tags v*") lets only repository admins create, move or delete v* tags',
    "the github-pages environment accepts deployments only from tags from v1.1.1 on",
)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top, cls.jobs = workflows.read_workflow("pages.yml")

    def test_only_a_published_release_or_a_manual_run_starts_the_workflow(self):
        # Pushes, pull requests and schedules never start it: tests.yml runs on those, and deploys nothing.
        self.assertEqual(self.top["on"][0], "", "triggers as a block, not a list")
        triggers = workflows.mapping(self.top["on"][1], 2, "on")
        self.assertEqual(set(triggers), {"release", "workflow_dispatch"})
        release = workflows.mapping(triggers["release"][1], 4, "release")
        self.assertEqual(set(release), {"types"})
        self.assertEqual(workflows.yaml_scalar(release["types"][0], "types"), ["published"])
        self.assertEqual(triggers["workflow_dispatch"], ("", []), "a manual run takes no inputs")

    def test_the_artifact_job_repeats_the_guards_before_anything_else(self):
        # The guard job is the guards alone: the event and ref check, the Release lookup and its check, a checkout with
        # every commit, the main check. The build job starts with the same steps, so nothing it runs from the checkout
        # precedes them.
        guard, build = self.jobs["guard"]["steps"], self.jobs["build"]["steps"]
        roles, names = [role for role, _ in GUARD_STEPS], [name for _, name in GUARD_STEPS]
        self.assertEqual([step["name"] for step in guard], [*names, APPROVE])
        self.assertEqual([step["name"] for step in build[: len(names)]], names)
        for index, role in enumerate(roles):
            if role == "checkout":
                continue
            self.assertEqual(guard[index]["lines"], build[index]["lines"], f"the {role} step differs")
            for job, steps in (("guard", guard), ("build", build)):
                with self.subTest(job=job, step=role):
                    self.assertIsNotNone(steps[index]["run"])
                    self.assertEqual(set(steps[index]["keys"]) - {"name", "env", "run"}, set(), "no if: or a shell")
        for job, steps in (("guard", guard), ("build", build)):
            with self.subTest(job=job):
                checkout = steps[roles.index("checkout")]
                self.assertTrue(checkout["keys"]["uses"].startswith("actions/checkout@"))
                self.assertIn("          fetch-depth: 0", checkout["lines"])
                self.assertIn("          persist-credentials: false", checkout["lines"])
        later = [step["keys"].get("uses") or step["run"] for step in build[len(names) :]]
        self.assertEqual(
            later[:2],
            [
                "python3 -I tests/test_pages.py",
                'python3 -I scripts/prepare_pages.py --output "$RUNNER_TEMP/deemo-site"',
            ],
        )
        self.assertTrue(later[2].startswith("actions/upload-pages-artifact@"))
        self.assertIn("          path: ${{ runner.temp }}/deemo-site", build[-1]["lines"])
        self.assertEqual(len(later), 3)

    def test_the_guard_job_gives_the_deploy_job_the_commit_it_approved_and_its_attempt(self):
        # The guard job's last step sets its outputs, so they are empty unless every guard step before it passed; the
        # deploy job's condition requires them to be the run's commit and its attempt (DEPLOY_CONDITION).
        guard = self.jobs["guard"]
        outputs = workflows.mapping(guard["keys"]["outputs"][1], 6, "outputs")
        self.assertEqual(
            {key: value for key, (value, _) in outputs.items()},
            {"commit": "${{ steps.approve.outputs.commit }}", "attempt": "${{ steps.approve.outputs.attempt }}"},
        )
        approve = guard["steps"][-1]
        self.assertEqual(approve["name"], APPROVE)
        self.assertEqual(set(approve["keys"]), {"name", "id", "env", "run"}, "no if: or continue-on-error")
        self.assertEqual(approve["keys"]["id"], "approve")
        self.assertEqual(approve["env"], {"SHA": "${{ github.sha }}"})
        self.assertIn("needs.guard.outputs.commit == github.sha", DEPLOY_CONDITION)
        self.assertIn("needs.guard.outputs.attempt == github.run_attempt", DEPLOY_CONDITION)

    def test_only_an_attempt_that_ran_the_guard_job_deploys(self):
        # A re-run of the deploy job alone keeps the guard job's outputs of the attempt it repeats and repeats no
        # Release lookup, so it deploys nothing; a re-run of the whole run can (RE_RUNS).
        condition = workflows.block_text(*self.jobs["deploy"]["keys"]["if"])
        for scenario, (contexts, deploys) in RE_RUNS.items():
            with self.subTest(scenario):
                self.assertIs(truthy(evaluate(condition, contexts)), deploys)

    def test_only_the_deploy_job_deploys_and_only_for_a_release_tag(self):
        deployers = [
            job_id
            for job_id, job in self.jobs.items()
            if any(step["keys"].get("uses", "").startswith("actions/deploy-pages@") for step in job["steps"])
        ]
        self.assertEqual(deployers, ["deploy"])
        self.assertEqual(workflows.block_text(*self.jobs["deploy"]["keys"]["if"]), DEPLOY_CONDITION)
        environment = workflows.mapping(self.jobs["deploy"]["keys"]["environment"][1], 6, "environment")
        self.assertEqual(environment["name"][0], "github-pages")
        concurrency = workflows.mapping(self.top["concurrency"][1], 2, "concurrency")
        self.assertEqual(
            {key: value for key, (value, _) in concurrency.items()},
            {"group": "github-pages", "cancel-in-progress": "false"},
        )


class ExpressionTests(unittest.TestCase):
    """evaluate() follows GitHub's documented rules for the expressions it reads and refuses every other one, so the
    deploy condition cannot pass these tests by being misread."""

    def test_values_compare_and_combine_as_on_github(self):
        for expression, contexts, value in (
            ("'Refs/Tags/V1' == 'refs/tags/v1'", {}, True),
            ("'1' == '01'", {}, False),
            ("a.b == '1'", {}, False),
            ("a.b == ''", {}, True),
            ("a.b == '2'", {"a.b": ""}, False),
            ("a.b == 'x'", {}, False),
            ("true == '1'", {}, True),
            ("false == null", {}, True),
            ("toJSON(false) == 'false'", {}, True),
            ("toJSON(a.b)", {}, "null"),
            ("toJSON(a.b)", {"a.b": "false"}, '"false"'),
            ("startsWith(a.b, 'REFS/')", {"a.b": "refs/tags/v1"}, True),
            ("startsWith(a.b, 'refs/')", {}, False),
            ("a.b && 'yes' || 'no'", {"a.b": ""}, "no"),
            ("a.b && 'yes' || 'no'", {"a.b": "x"}, "yes"),
            ("('it''s' || a.b) == 'IT''S'", {}, True),
        ):
            with self.subTest(expression, contexts=contexts):
                self.assertEqual(evaluate(expression, contexts), value)

    def test_other_syntax_is_refused(self):
        for expression in (
            "a != b",
            "!a",
            "a[0]",
            "1 == 1",
            "a == b == c",
            "fromJSON(a)",
            "startsWith(a)",
            "(a",
            "a)",
            "",
        ):
            with self.subTest(expression), self.assertRaises(ValueError):
                evaluate(expression, {})


class DocumentationTests(unittest.TestCase):
    def section(self, readme):
        text = (ROOT / readme).read_text(encoding="utf-8")
        return text[text.index(DEPLOYMENT_SECTIONS[readme]) :].split("\n## ", 1)[0]

    def header(self):
        """The header comment of pages.yml, its lines joined with spaces."""
        lines = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8").splitlines()
        return " ".join(line.removeprefix("# ") for line in lines[: lines.index("on:")] if line.startswith("#"))

    def test_both_readmes_describe_the_release_model(self):
        # Releases are vX.Y.Z tags, a manual run on the tag redeploys one, and both READMEs name the same current
        # release, linked to its page; the budget sentence stays.
        current = set()
        for readme in DEPLOYMENT_SECTIONS:
            with self.subTest(document=readme):
                section = self.section(readme)
                self.assertIn("`vX.Y.Z`", section)
                self.assertRegex(section, rf"`gh workflow run pages\.yml --ref {VERSION}`")
                (release,) = CURRENT_RELEASE.findall(section)
                self.assertEqual(release[0], release[1], "the link text names the release it links")
                current.add((release[0], release[2]))
                self.assertIn("`tests/test_pages.py`", section)
                self.assertIn(f"{pages.MAX_SITE_BYTES // 1_000_000} MB", section)
        self.assertEqual(len(current), 1, "both READMEs name the same current release and date")

    def test_both_readmes_and_the_workflow_state_the_release_rules(self):
        for readme, phrases in RELEASE_RULES.items():
            section = self.section(readme)
            text = (ROOT / readme).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(document=readme, phrase=phrase):
                    self.assertIn(phrase, section)
            for phrase in NO_RELEASE_LOOKUP[readme]:
                with self.subTest(document=readme, stale=phrase):
                    self.assertNotIn(phrase, text)
        header = self.header()
        for phrase in HEADER_RULES:
            with self.subTest(document="pages.yml", phrase=phrase):
                self.assertIn(phrase, header)
        _, jobs = workflows.read_workflow("pages.yml")
        self.assertEqual(workflows.yaml_scalar(jobs["guard"]["keys"]["name"][0], "name"), GUARD_JOB)

    def test_a_manual_run_is_shown_only_on_a_tag_whose_workflow_checks_the_release(self):
        # A run takes pages.yml from the tag's commit, and the pages.yml of a tag older than FIRST_CHECKED_TAG does not
        # look up the Release: an example on such a tag would not do what the text around it says.
        def version(tag):
            return tuple(int(number) for number in tag.removeprefix("v").split("."))

        for document in OLDER_TAGS:
            text = self.header() if document.endswith(".yml") else self.section(document)
            for tag in MANUAL_RUN.findall(text):
                with self.subTest(document=document, example=tag):
                    self.assertGreaterEqual(version(tag), version(FIRST_CHECKED_TAG))
            for phrase in OLDER_TAGS[document]:
                with self.subTest(document=document, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_no_readme_says_a_push_to_main_deploys(self):
        for readme, phrases in PUSH_DEPLOYED.items():
            text = (ROOT / readme).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(document=readme, phrase=phrase):
                    self.assertNotIn(phrase, text)


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        self.output = Path(self.temporary.name) / "site"
        self.tracked = set(pages.PUBLIC_FILES)
        for relative in self.tracked:
            (self.root / relative).write_bytes(b"public")

    def add(self, relative, tracked=True):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample")
        if tracked:
            self.tracked.add(relative)

    def test_only_tracked_allowlisted_files_are_copied(self):
        published = {
            "assets/public/art.png",
            "data/catalog.js",
            "src/archive.js",
            "docs/sources-wikis.md",
            "licenses/README.md",
        }
        unpublished = {
            "scripts/build_catalog.py",
            "templates/slideshow.html",
            "tests/test_pages.py",
            ".github/workflows/pages.yml",
            ".git/config",
            "assets/.env",
            "src/__pycache__/cached.pyc",
            "requirements.txt",
            "pyproject.toml",
            ".gitattributes",
        }
        for relative in sorted(published | unpublished):
            self.add(relative)
        self.add("assets/local-only.png", tracked=False)
        result = pages.prepare(self.root, self.output, self.tracked)
        actual = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        # The root files are spelled out rather than taken from pages.PUBLIC_FILES, so widening the allowlist fails.
        root_files = {
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
        expected = root_files | published
        self.assertEqual(actual, expected)
        self.assertEqual(result["files"], len(expected))
        for name in actual:
            self.assertEqual((self.output / name).read_bytes(), (self.root / name).read_bytes())

    def test_missing_entry_file_fails(self):
        self.tracked.remove("index.html")
        with self.assertRaisesRegex(ValueError, "Required public files"):
            pages.prepare(self.root, self.output, self.tracked)

    def test_symlinks_are_rejected(self):
        (self.root / "assets").mkdir()
        (self.root / "assets/leak").symlink_to(self.root / "NOTICE")
        self.tracked.add("assets/leak")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            pages.prepare(self.root, self.output, self.tracked)

    def test_a_file_under_a_symlinked_directory_is_rejected(self):
        # The file itself is no symlink; a directory on its path is, and leads to a directory that is not published.
        self.add("templates/notes.txt")
        (self.root / "assets").mkdir()
        (self.root / "assets/linked").symlink_to(self.root / "templates", target_is_directory=True)
        self.tracked.add("assets/linked/notes.txt")
        with self.assertRaisesRegex(ValueError, "Symlinks must not be published: assets/linked/notes.txt"):
            pages.prepare(self.root, self.output, self.tracked)
        self.assertFalse(self.output.exists())

    def test_the_budget_counts_every_published_byte(self):
        self.add("assets/public/large.png")
        (self.root / "assets/public/large.png").write_bytes(bytes(4096))
        total = sum((self.root / name).stat().st_size for name in self.tracked)
        with patch.object(pages, "MAX_SITE_BYTES", total - 1), self.assertRaisesRegex(ValueError, f"budget: {total}$"):
            pages.prepare(self.root, self.output, self.tracked)
        self.assertFalse(self.output.exists())
        with patch.object(pages, "MAX_SITE_BYTES", total):
            self.assertEqual(pages.prepare(self.root, self.output, self.tracked)["bytes"], total)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            pages.prepare(self.root, self.output, self.tracked)

    def test_repository_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            pages.prepare(self.root, self.root / "site", self.tracked)

    def test_oversized_payload_is_rejected_before_copy(self):
        with patch.object(pages, "MAX_SITE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "budget"):
                pages.prepare(self.root, self.output, self.tracked)
        self.assertFalse(self.output.exists())


def in_git_checkout():
    if not shutil.which("git"):
        return False
    try:
        top = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return Path(top.stdout.strip()).resolve() == ROOT


@unittest.skipUnless(in_git_checkout(), "needs git and a Git checkout of this repository")
class RepositoryTests(unittest.TestCase):
    def test_the_tracked_site_fits_the_pages_budget(self):
        # The deployment measures the payload only for a release; this test runs on every push and pull request
        # (tests.yml), so a change that would push the site past the budget fails before it is merged.
        tracked = pages.tracked_files(ROOT)
        try:
            selected, total = pages.payload(ROOT, tracked)
        except ValueError as error:
            # payload() refuses an over-budget site itself; report that as this test's failure, with the excess.
            measured = re.search(r"budget: ([0-9]+)$", str(error))
            if measured is None:
                raise
            excess = int(measured.group(1)) - pages.MAX_SITE_BYTES
            raise self.failureException(f"{excess} bytes over the Pages budget: remove material") from None
        self.assertIn(Path("index.html"), selected)
        self.assertLessEqual(total, pages.MAX_SITE_BYTES)
        # The check measures what is published: one byte less of budget and the same files are refused.
        with patch.object(pages, "MAX_SITE_BYTES", total - 1), self.assertRaisesRegex(ValueError, f"budget: {total}$"):
            pages.payload(ROOT, tracked)

    def test_an_over_budget_site_fails_the_budget_test_with_its_excess(self):
        # Over the budget, the test above must fail (not stop with an error) and say how many bytes to remove.
        result = unittest.TestResult()
        with patch.object(pages, "MAX_SITE_BYTES", 1):
            RepositoryTests("test_the_tracked_site_fits_the_pages_budget").run(result)
        self.assertEqual(result.errors, [])
        (failure,) = result.failures
        excess = re.search(r"AssertionError: ([0-9]+) bytes over the Pages budget: remove material$", failure[1], re.M)
        self.assertIsNotNone(excess, failure[1])
        total = sum((ROOT / path).stat().st_size for path in pages.select_files(ROOT, pages.tracked_files(ROOT)))
        self.assertEqual(int(excess.group(1)), total - 1)


if __name__ == "__main__":
    unittest.main()
