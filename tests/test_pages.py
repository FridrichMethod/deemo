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
# approved and the attempt of the run they ran in, both empty unless every one of them passed. A re-run that leaves out
# the guard job, such as one of the deploy job alone, keeps the guard job's outputs of the attempt it repeats (even when
# it runs the build job's Release lookup again), so the attempt must be the run's own (RE_RUNS).
DEPLOY_CONDITION = (
    "startsWith(github.ref, 'refs/tags/v') && needs.guard.outputs.commit == github.sha && "
    "needs.guard.outputs.attempt == github.run_attempt && "
    "(github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'release' && github.event.action == 'published' && "
    "toJSON(github.event.release.prerelease) == 'false'))"
)
# The part of GitHub's expression language that the deploy condition uses: string, true, false and null literals,
# context properties (null when absent), ==, && and || (each gives one of its operands, and takes false, 0, NaN, '' and
# null for false, and anything else, an array or an object included, for true), parentheses, startsWith() and toJSON().
# == compares two strings ignoring case and two values of one other type as they are; values of two types become
# numbers first (null and false 0, true 1, a number itself, '' 0, another string its JSON number or NaN, an array or an
# object NaN; NaN equals nothing). startsWith() casts both arguments to strings (null to '') and ignores case. Any other
# syntax is an error.
EXPRESSION_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(==|&&|\|\||[(),])|([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*))")
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
LITERALS = {"true": True, "false": False, "null": None}


def as_number(value):
    if value is None or isinstance(value, bool):
        return float(bool(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return 0.0 if value == "" else float(value) if JSON_NUMBER.fullmatch(value) else math.nan
    return math.nan  # an array or an object


def as_text(value):
    return "" if value is None else str(value).lower() if isinstance(value, bool) else value


def truthy(value):
    if value is None or isinstance(value, bool):
        return value is True
    if isinstance(value, (int, float)):
        return value != 0 and not math.isnan(value)
    return value != ""  # a string, or an array or an object, which is never ''


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
# whole run runs the guard job again, which records the new attempt. A re-run of the deploy job alone, or of the failed
# jobs after only a later job than the guard job failed, leaves the guard job out: even when it runs the build job,
# whose guard steps look the Release up again, the deploy job sees the guard job's outputs of the attempt it repeats. A
# guard job that refused the run recorded nothing.
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
# a release's run included, that a re-run without the guard job (named as on the Actions page) deploys nothing and why,
# the tag form, and the two repository settings that limit which tags can deploy.
GUARD_JOB = "Release guard"
RELEASE_RULES = {
    "README.md": (
        "Every run, whether a release or a manual run started it, first checks",
        "one read-only request to the GitHub API",
        "A re-run keeps the event of the run it repeats",
        "also refuses a re-run of a release's run",
        f"A re-run that leaves out the {GUARD_JOB} job, such as a re-run of the deploy job alone, deploys nothing,"
        " because the deploy job requires the guard job's record of the current attempt; re-run the whole run.",
        "a run on a tag without such a Release",
        "three numbers without leading zeros",
        "`v01.1.0`",
        'A repository ruleset ("Release tags v*"',
        "lets only repository admins create, move or delete `v*` tags",
        "the rules of the `github-pages` environment accept deployments only from tags from `v1.1.1` on",
        "these rules keep anyone but an admin from deploying through the `github-pages` environment with a workflow of"
        " their own",
    ),
    "README.zh-CN.md": (
        "每次运行，无论由发布版还是手动触发，都会先",
        "向 GitHub API 发出一次只读请求",
        "重新运行（re-run）沿用被重新运行的那次运行的事件",
        "也会被这项检查拒绝",
        f"不包括 {GUARD_JOB} 任务的重新运行（例如只重新运行部署任务）不会部署任何内容，"
        f"因为部署任务要求有 {GUARD_JOB} 任务对当前这次尝试（attempt）的记录；应重新运行整次运行。",
        "在没有这样的 Release 的标签上的运行",
        "三个不带前导零的数字",
        "`v01.1.0`",
        '仓库规则集（ruleset）"Release tags v*"',
        "只允许仓库管理员创建、移动或删除 `v*` 标签",
        "`github-pages` 环境的部署规则只接受 `v1.1.1` 及之后的标签",
        "这些规则使管理员以外的人无法用自己的工作流经 `github-pages` 环境部署",
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
    "a re-run that leaves out the guard job, such as one of the deploy job alone, deploys nothing, because the deploy"
    " job requires the guard job's record of the current attempt; re-run the whole run",
    "the commit the guard job approved in the same attempt",
    "three numbers without leading zeros",
    'A repository ruleset ("Release tags v*") lets only repository admins create, move or delete v* tags',
    "the github-pages environment accepts deployments only from tags from v1.1.1 on",
    "the two keep anyone but an admin from deploying through the github-pages environment with a workflow of their own",
)
# What the READMEs and the comments of pages.yml said before the github-pages environment accepted deployments only
# from tags from v1.1.1 on (2026-10-12): an environment rule that allows every v* tag or main, a request to run only
# tags from v1.1.1 on by hand, and a manual run that deploys a release again without looking up its Release.
RETIRED_DEPLOYMENT_RULES = {
    "README.md": (
        "allows `v*` tags",
        "must allow `v*` tags",
        "allows `main`",
        "should be run by hand",
        "redeploy",
        "deploys that release again",
        "deploys that tag's commit again",
    ),
    "README.zh-CN.md": ("允许 `v*` 标签", "允许 `main`", "只应手动运行", "重新部署"),
    ".github/workflows/pages.yml": (
        "allows v* tags",
        "allows main",
        "should be run by hand",
        "redeploy",
        "deploys it again",
        "deploys that commit again",
        "does not look up the tag's Release, so",
    ),
}
# What a draft of them said of a re-run that leaves out the guard job: that it looks up no Release. One that runs the
# build job does look it up; it deploys nothing all the same. The phrases of a language are kept out of every document
# in that language, read without case and with the line breaks of its comments joined (flowing()).
DOCUMENT_LANGUAGES = {"README.md": "en", "README.zh-CN.md": "zh", ".github/workflows/pages.yml": "en"}
NO_LOOKUP_ON_A_RE_RUN = {
    "en": re.compile(
        r"does not look (?:up )?the Release (?:up )?again|looks up no Release|repeats no (?:Release )?lookup", re.I
    ),
    "zh": re.compile(r"不会再次?查询 ?Release", re.I),
}


def flowing(text):
    """text with each run of white space and comment marks (#) taken as one space, so a phrase reads the same when the
    line breaks of a comment fall inside it."""
    return re.sub(r"[\s#]+", " ", text)


# Where a sentence of a deployment section or of the header comment ends: after a full stop and a space, but not after
# "e.g.", "i.e.", "etc." or "cf.", or after 。.
SENTENCE_END = re.compile(r"(?<=\.)(?<![Ee]\.g\.)(?<![Ii]\.e\.)(?<!etc\.)(?<![Cc]f\.)\s+|(?<=。)")
# The same retired rules in other words. A sentence of a deployment section or of the header comment brings one back
# if it matches every pattern of that rule, read without case: it names the environment and main, or the environment
# and every (any, all, each) v* tag; it says a manual run is for tags from some vX.Y.Z on (onwards, or later, and
# newer, 及之后, 或之后, 以后, 或更新, 起; "on" only after "from", as "a manual run on v1.2.0 on the Actions page" is
# not); or it says a run deploys again (once more, 再次, 再一次, 又一次), unless its clause names a re-run of the whole
# run before that, which does deploy the tag's commit again.
ENVIRONMENT = re.compile(r"environment|环境", re.I)
RETIRED_SENTENCES = {
    "the environment and main": (ENVIRONMENT, re.compile(r"(?<![a-z])main(?![a-z])", re.I)),
    "the environment and every v* tag": (
        ENVIRONMENT,
        re.compile(
            r"\b(?:every|any|all|each)\W+(?:\w+\W+)?v\*|(?:所有|任何|任意|全部|一切|每个)\W*(?:的\W*)?v\*", re.I
        ),
    ),
    "a manual run for tags from a vX.Y.Z on": (
        re.compile(r"\bmanual(?:ly)?\b|\bby hand\b|手动", re.I),
        re.compile(
            rf"\bfrom\W+(?:\w+\W+)?{VERSION}`?\s*on\b"
            rf"|{VERSION}`?\s*(?:onwards?\b|(?:or|and) (?:later|newer)\b|[及或]?(?:之后|以后)|或更新|起)",
            re.I,
        ),
    ),
    "a run that deploys again": (
        re.compile(
            # A clause starts at the start of the sentence or after , ; : (or ，；：).
            r"\bre-?deploy|(?:^|[,;:，；：])(?:(?!re-run of the whole run|重新运行整次运行)[\s\S])*"
            r"(?:\bdeploy(?:s|ed|ing)?\b(?! job)[^,;:]*\b(?:again|once more)\b|(?:[再又]一次|再次?|重新|又)部署)",
            re.I,
        ),
    ),
}


def retired_rules(sentence):
    """The rules of RETIRED_SENTENCES whose every pattern sentence matches."""
    return [
        rule for rule, patterns in RETIRED_SENTENCES.items() if all(pattern.search(sentence) for pattern in patterns)
    ]


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
        # A re-run that leaves out the guard job, such as one of the deploy job alone, keeps the guard job's outputs of
        # the attempt it repeats, so it deploys nothing; a re-run of the whole run can (RE_RUNS).
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
            ("a.b && 'yes' || 'no'", {}, "no"),
            # A context may hold a number: it stays that number, sign included, and only 0 and NaN are false.
            ("a.b == c.d", {"a.b": "2", "c.d": 2}, True),
            ("a.b == c.d", {"a.b": "1", "c.d": 2}, False),
            ("a.b == '0.5'", {"a.b": 0.5}, True),
            ("a.b == '-1'", {"a.b": -1}, True),
            ("a.b == true", {"a.b": 2}, False),
            ("a.b == null", {"a.b": 0}, True),
            ("a.b && 'yes' || 'no'", {"a.b": 2}, "yes"),
            ("a.b && 'yes' || 'no'", {"a.b": 0}, "no"),
            ("a.b && 'yes' || 'no'", {"a.b": math.nan}, "no"),
            # Or an array or an object: it is true, and NaN next to a value of another type.
            ("a.b && 'yes' || 'no'", {"a.b": []}, "yes"),
            ("a.b && 'yes' || 'no'", {"a.b": {}}, "yes"),
            ("a.b == null", {"a.b": []}, False),
            ("a.b == ''", {"a.b": {}}, False),
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

    def test_no_document_brings_back_the_retired_deployment_rules(self):
        # The github-pages environment accepts deployments only from tags from v1.1.1 on: not every v* tag, and not
        # main; that is a rule the environment enforces, not a request about manual runs, and every run looks up the
        # Release, so none deploys a release again without it.
        for document, phrases in RETIRED_DEPLOYMENT_RULES.items():
            text = (ROOT / document).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(document=document, stale=phrase):
                    self.assertNotIn(phrase, text)
            stale = NO_LOOKUP_ON_A_RE_RUN[DOCUMENT_LANGUAGES[document]]
            with self.subTest(document=document, stale=stale.pattern):
                self.assertEqual(stale.findall(flowing(text)), [])
            described = self.header() if document.endswith(".yml") else self.section(document)
            for sentence in SENTENCE_END.split(described):
                with self.subTest(document=document, sentence=sentence[:60]):
                    self.assertEqual(retired_rules(sentence), [])

    def test_a_re_run_that_looks_up_no_release_is_recognised_in_other_words(self):
        # Each draft phrase and each of its other forms is found in its language, also across the line break of a
        # comment; what the documents say of a run on an older tag is not.
        for language, text, found in (
            ("en", "A re-run of the deploy job alone does not look the Release up again.", True),
            ("en", "A re-run of the deploy job alone does not look up the Release again.", True),
            ("en", "A re-run without the guard job looks up no Release.", True),
            ("en", "A re-run of later jobs repeats no lookup.", True),
            ("en", "A re-run of later jobs repeats no Release lookup.", True),
            ("en", "A RE-RUN OF FAILED JOBS DOES NOT LOOK THE RELEASE UP AGAIN.", True),
            ("en", "# A re-run of failed jobs does not look the\n# Release up again.", True),
            ("en", OLDER_TAGS[".github/workflows/pages.yml"][0], False),
            ("zh", "只重新运行部署任务不会再次查询 Release。", True),
            ("zh", "只重新运行部署任务不会再查询 Release。", True),
            ("zh", "只重新运行部署任务不会再查询Release。", True),
            ("zh", "只重新运行部署任务不会再查询 release。", True),
            ("zh", OLDER_TAGS["README.zh-CN.md"][0], False),
        ):
            with self.subTest(text, language=language):
                self.assertIs(bool(NO_LOOKUP_ON_A_RE_RUN[language].search(flowing(text))), found)

    def test_the_retired_rules_are_recognised_in_other_words(self):
        # Each text, split into sentences as the documents are, brings back the rules named with it, and only those: one
        # sample for each form a pattern knows, and the true sentences next to them that it must not take for one.
        main, every, manual, again = (
            "the environment and main",
            "the environment and every v* tag",
            "a manual run for tags from a vX.Y.Z on",
            "a run that deploys again",
        )
        for text, rules in (
            ("Environment rules also accept `main`.", [main]),
            ("Main is accepted by the environment too.", [main]),
            ("Deployment environments also accept `main`.", [main]),
            ("`github-pages` 环境也接受 `main`。", [main]),
            # main must not touch a Latin letter on either side; a Chinese character may.
            ("部署环境也接受main分支。", [main]),
            ("The `github-pages` environment remains as it is.", []),
            ("The environment's domain is github.io.", []),
            ("The environment is maintained by the admins.", []),
            # No sentence ends at an abbreviation.
            ("The `github-pages` environment, e.g. for `main`, accepts it.", [main]),
            ("The environment covers one branch, E.g. `main`.", [main]),
            ("The environment, i.e. its rules, admits `main`.", [main]),
            ("The environment covers one branch, I.e. `main`.", [main]),
            ("The environment accepts tags etc. and also `main`.", [main]),
            ("The environment (cf. `main`) accepts it.", [main]),
            ("The environment accepts it (Cf. `main`).", [main]),
            ("The `github-pages` environment allows every `v*` tag.", [every]),
            ("The github-pages environment allows any v* tag.", [every]),
            ("The environment accepts all `v*` tags.", [every]),
            ("The environment accepts each `v*` tag.", [every]),
            ("Every `v*` tag is accepted by the environment.", [every]),
            ("The environment accepts every tag `v*`.", [every]),
            ("`github-pages` 环境允许所有 `v*` 标签。", [every]),
            ("环境允许任何 `v*` 标签。", [every]),
            ("环境接受任意 v* 标签。", [every]),
            ("环境接受全部的 `v*` 标签。", [every]),
            ("环境接受一切 `v*` 标签。", [every]),
            ("环境接受每个 `v*` 标签。", [every]),
            # 所有 `v*` 标签都受规则集保护，环境另有规则。 is true and matches too: only what its last clause means
            # tells it from 所有 `v*` 标签都受规则集保护，环境也接受它们。, so a sentence like it must be put another way.
            ("Only tags from v1.1.1 on should be run by hand.", [manual]),
            ("Only tags from `v1.1.1` on are to be run manually.", [manual]),
            ("Run by hand only the tags from release `v1.2.0` on.", [manual]),
            ("Manual runs are meant for tags v1.1.1 onwards.", [manual]),
            ("Manual runs are meant for tags v1.1.1 onward.", [manual]),
            ("Only run tags v1.1.1 or later manually.", [manual]),
            ("Run by hand only tags `v1.1.1` and newer.", [manual]),
            ("MANUAL runs are for tags FROM v1.2.0 ON.", [manual]),
            ("只能手动运行 `v1.1.1` 及之后的标签。", [manual]),
            ("只手动运行 v1.1.1 或之后的标签。", [manual]),
            ("手动运行只适用于 `v1.1.1` 以后的标签。", [manual]),
            ("只应对 `v1.1.1` 或更新的标签手动运行。", [manual]),
            ("手动运行仅限 `v1.1.1` 起的标签。", [manual]),
            # "on" after a tag counts only after "from".
            ("A manual run on `v1.2.0` on the Actions page deploys that tag's commit.", []),
            ("A manual run deploys that tag's commit again.", [again]),
            ("A manual run deploys the release once more.", [again]),
            ("A manual run deployed the tag again.", [again]),
            ("Deploying that tag again takes a manual run.", [again]),
            ("Manual runs deploy it again.", [again]),
            ("A manual run re-deploys the tag.", [again]),
            ("A manual run redeploys a released tag.", [again]),
            ("手动运行会再次部署该标签。", [again]),
            ("手动运行会把该标签的提交再部署一次。", [again]),
            ("手动运行会再一次部署该标签。", [again]),
            ("手动运行会又一次部署该标签。", [again]),
            ("手动运行会重新部署该提交。", [again]),
            ("手动运行又部署了该标签。", [again]),
            # The deploy job running again is no run that deploys again, and "again" counts only in the clause of the
            # deployment.
            ("A re-run of the deploy job alone runs the deploy job again and deploys nothing.", []),
            ("A run deploys only a release; to try again, re-run the whole run.", []),
            # A re-run of the whole run does deploy again, but only a clause that names it first is about it.
            ("A re-run of the whole run deploys the tag's commit again.", []),
            ("重新运行整次运行会再次部署该标签的提交。", []),
            ("A manual run deploys it again, as a re-run of the whole run does.", [again]),
            ("A re-run of the whole run checks the Release, and a manual run deploys it again.", [again]),
            ("A re-run of the whole run checks the Release; a manual run deploys it again.", [again]),
            ("A re-run of the whole run is one way: a manual run deploys it again.", [again]),
            ("重新运行整次运行会查询 Release，手动运行会再次部署该标签。", [again]),
            ("重新运行整次运行会查询 Release；手动运行会再次部署该标签。", [again]),
            ("重新运行整次运行是一种办法：手动运行会再次部署该标签。", [again]),
        ):
            with self.subTest(text):
                self.assertEqual([found for piece in SENTENCE_END.split(text) for found in retired_rules(piece)], rules)


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
