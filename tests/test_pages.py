"""Ensure only a release is deployed, and only tracked, allowlisted website files reach the GitHub Pages payload."""

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
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
# toJSON() turns false into 'false' and a missing flag into 'null'. The guard job's output is the commit its steps
# approved, which is empty unless every one of them passed.
DEPLOY_CONDITION = (
    "startsWith(github.ref, 'refs/tags/v') && needs.guard.outputs.commit == github.sha && "
    "(github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'release' && github.event.action == 'published' && "
    "toJSON(github.event.release.prerelease) == 'false'))"
)
# The only contexts the guard steps may read. GitHub renders a property the event does not have as an empty string.
# github.token is the one context that is not about the run's event, ref or commit: the job's token, which can only read
# (contents: read). Only the Release lookup may read it (TOKEN_READERS).
GUARD_CONTEXTS = (
    "github.event_name",
    "github.event.action",
    "github.ref",
    "github.event.release.tag_name",
    "github.event.release.prerelease",
    "github.sha",
    "github.token",
)
TOKEN_READERS = {"lookup"}
EXPRESSION = re.compile(r"\$\{\{ ([a-z_.]+) \}\}")
# The guard steps of both jobs by role, in the order they run. In the guard tests the Checkout class below stands in for
# the checkout step.
GUARD_STEPS = (
    ("event", "Refuse anything but a published release or a manual run on a vX.Y.Z tag"),
    ("lookup", "Look up the GitHub Release of the tag"),
    ("release", "Refuse a tag without a published Release that is not a prerelease"),
    ("checkout", "Checkout"),
    ("main", "Refuse a release commit that is not on main"),
)
# The guard job's last step, which gives the commit that every guard step passed as the job's output.
APPROVE = "Approve the release commit"
# The repository the runner names in GITHUB_REPOSITORY, the job token behind github.token, and the headers the Release
# lookup sends with them.
REPOSITORY = "FridrichMethod/deemo"
TOKEN = "ghs_job-token-of-the-guard-tests"
LOOKUP_HEADERS = [
    f"Authorization: Bearer {TOKEN}",
    "Accept: application/vnd.github+json",
    "X-GitHub-Api-Version: 2022-11-28",
]
# The longest --max-time (whole seconds) the lookup may give curl: a hung request then fails the lookup, and with it the
# run, within two minutes instead of at the job's timeout.
LOOKUP_MAX_SECONDS = 120
MISSING = object()  # a field release_response() leaves out
# curl as the Release lookup calls it. It accepts only the options of one plain GET (an option that could change the
# method, follow a redirect or skip the TLS checks is an error), logs the request to $FAKE_CURL_LOG and answers with
# $FAKE_RESPONSE: {"status", "body"}, or {"error": [curl's exit code, its message]} for a request that got no answer.
FAKE_CURL = r"""
import json
import os
import sys

FLAGS = {"--silent", "--show-error"}
options = {"--proto": [], "--max-time": [], "--header": [], "--write-out": []}
urls = []
arguments = iter(sys.argv[1:])
for argument in arguments:
    if argument in FLAGS:
        continue
    if argument in options:
        options[argument].append(next(arguments))
    elif argument.startswith("-"):
        sys.exit(f"curl (fake): unsupported option {argument}")
    else:
        urls.append(argument)
headers = []
for header in options["--header"]:
    headers += sys.stdin.read().splitlines() if header == "@-" else [header]
with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "urls": urls, "headers": headers, "options": options}) + "\n")
with open(os.environ["FAKE_RESPONSE"], encoding="utf-8") as file:
    response = json.load(file)
code, message = response.get("error") or (0, "")
status = "000" if code else response["status"]
written = "".join(options["--write-out"]).replace("\\n", "\n").replace("%{http_code}", status)
sys.stdout.write(("" if code else response["body"]) + written)
if code:
    print(f"curl: ({code}) {message}", file=sys.stderr)
    sys.exit(code)
"""
COMMITTER = {"NAME": "Release test", "EMAIL": "release@example.invalid", "DATE": "2026-10-11T00:00:00Z"}
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
# a release's run included, the tag form, and the two repository settings that limit which tags can deploy.
RELEASE_RULES = {
    "README.md": (
        "Every run, whether a release or a manual run started it, first checks",
        "one read-only request to the GitHub API",
        "A re-run keeps the event of the run it repeats",
        "also refuses a re-run of a release's run",
        "a run on a tag without such a Release",
        "three numbers without leading zeros",
        "`v01.1.0`",
        'A repository ruleset ("Release tags v*"',
        "lets only repository admins create, move or delete `v*` tags",
        "the rule of the `github-pages` environment that allows `v*` tags",
    ),
    "README.zh-CN.md": (
        "每次运行，无论由发布版还是手动触发，都会先",
        "向 GitHub API 发出一次只读请求",
        "重新运行（re-run）沿用被重新运行的那次运行的事件",
        "也会被这项检查拒绝",
        "在没有这样的 Release 的标签上的运行",
        "三个不带前导零的数字",
        "`v01.1.0`",
        '仓库规则集（ruleset）"Release tags v*"',
        "只允许仓库管理员创建、移动或删除 `v*` 标签",
        "`github-pages` 环境中允许 `v*` 标签的部署规则",
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
        "a run on a tag older than `v1.1.1` uses that tag's own workflow, which does not look up the Release",
        "only tags from `v1.1.1` on should be run by hand",
    ),
    "README.zh-CN.md": (
        "在早于 `v1.1.1` 的标签上的运行使用该标签自己的工作流，它不会查询 Release",
        "只应手动运行 `v1.1.1` 及之后的标签",
    ),
    ".github/workflows/pages.yml": (
        "a run on a tag older than v1.1.1 takes that tag's own workflow, which does not look up the Release",
        "only tags from v1.1.1 on should be run by hand",
    ),
}
# The same rules in the header comment of pages.yml, whose lines are joined with spaces.
HEADER_RULES = (
    "a published GitHub Release, not marked as a prerelease",
    "Every run, whichever of the two started it, first asks for the tag's Release",
    "one read-only GitHub API request",
    "a re-run keeps the event of the run it repeats",
    "three numbers without leading zeros",
    'A repository ruleset ("Release tags v*") lets only repository admins create, move or delete v* tags',
    "the github-pages environment allows v* tags",
)


def published(tag="v1.1.0", commit="released", **changes):
    """The contexts of a run started by publishing a full release of tag."""
    contexts = {
        "github.event_name": "release",
        "github.event.action": "published",
        "github.ref": f"refs/tags/{tag}",
        "github.event.release.tag_name": tag,
        "github.event.release.prerelease": "false",
        "github.sha": commit,
    }
    return {**contexts, **changes}


def dispatched(ref="refs/tags/v1.1.0", commit="released", event="workflow_dispatch"):
    """The contexts of a run on ref that no release started: a manual run, or another event."""
    return {"github.event_name": event, "github.ref": ref, "github.sha": commit}


def runs(tag="v1.1.0", commit="released"):
    """The contexts of a manual run on tag and of a run started by publishing a full release of tag: the guards judge
    the Release of either by the same lookup."""
    return dispatched(f"refs/tags/{tag}", commit), published(tag, commit)


def answer(status, body):
    """A response to the Release lookup: its HTTP status and its body."""
    return {"status": status, "body": body}


def release_response(tag="v1.1.0", status="200", **changes):
    """GitHub's answer to GET /repos/{owner}/{repo}/releases/tags/{tag} for the published full Release of tag, with
    changes to its fields (MISSING leaves one out)."""
    release = {
        "url": f"https://api.github.com/repos/{REPOSITORY}/releases/1",
        "tag_name": tag,
        "target_commitish": "main",
        "name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-11T00:00:00Z",
        **changes,
    }
    return answer(status, json.dumps({key: value for key, value in release.items() if value is not MISSING}))


# What the endpoint answers for a tag without a published Release: no Release, or a draft only.
NOT_FOUND = answer(
    "404",
    json.dumps(
        {
            "message": "Not Found",
            "documentation_url": "https://docs.github.com/rest/releases/releases#get-a-release-by-tag-name",
            "status": "404",
        }
    ),
)
# A request that got no answer.
UNREACHABLE = {"error": [6, "Could not resolve host: api.github.com"]}


class Checkout:
    """A repository as actions/checkout with fetch-depth: 0 leaves it: origin's branches under refs/remotes/origin/,
    every tag, no local branch, and HEAD detached at the run's commit. The commits:

        root - released - tip     main (refs/remotes/origin/main); v1.1.0 tags released, v1.2.0 tip
            \\
             aside                another branch only (refs/remotes/origin/feature); v1.3.0 tags it

    Two more refs name aside where a careless guard would look for main: a local branch main, and a tag named
    origin/main, which `git rev-parse origin/main` prefers to the remote-tracking branch. With main=False, origin has
    no main branch at all."""

    def __init__(self, directory, main=True):
        self.directory = directory
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(directory),
            "GIT_CONFIG_NOSYSTEM": "1",
            **{f"GIT_{role}_{key}": value for role in ("AUTHOR", "COMMITTER") for key, value in COMMITTER.items()},
        }
        self.git("init", "--quiet")
        tree = self.git("mktree")
        root = self.git("commit-tree", tree, "-m", "root")
        released = self.git("commit-tree", tree, "-p", root, "-m", "released")
        tip = self.git("commit-tree", tree, "-p", released, "-m", "tip")
        aside = self.git("commit-tree", tree, "-p", root, "-m", "aside")
        self.commits = {"released": released, "tip": tip, "aside": aside}
        refs = {
            "refs/remotes/origin/feature": aside,
            "refs/heads/main": aside,
            "refs/tags/origin/main": aside,
            "refs/tags/v1.1.0": released,
            "refs/tags/v1.2.0": tip,
            "refs/tags/v1.3.0": aside,
        }
        if main:
            refs["refs/remotes/origin/main"] = tip
        for ref, commit in refs.items():
            self.git("update-ref", ref, commit)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.directory, env=self.env, input="", capture_output=True, text=True, check=True
        ).stdout.strip()

    def detach(self, commit):
        self.git("update-ref", "--no-deref", "HEAD", self.commits[commit])


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

    def test_the_guard_job_gives_the_deploy_job_the_commit_it_approved(self):
        # The guard job's last step sets its output, so the output is empty unless every guard step before it passed;
        # the deploy job's condition requires it to be the run's commit (DEPLOY_CONDITION).
        guard = self.jobs["guard"]
        outputs = workflows.mapping(guard["keys"]["outputs"][1], 6, "outputs")
        self.assertEqual(
            {key: value for key, (value, _) in outputs.items()}, {"commit": "${{ steps.approve.outputs.commit }}"}
        )
        approve = guard["steps"][-1]
        self.assertEqual(approve["name"], APPROVE)
        self.assertEqual(set(approve["keys"]), {"name", "id", "env", "run"}, "no if: or continue-on-error")
        self.assertEqual(approve["keys"]["id"], "approve")
        self.assertEqual(approve["env"], {"SHA": "${{ github.sha }}"})
        self.assertIn("needs.guard.outputs.commit == github.sha", DEPLOY_CONDITION)

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


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to run the guard steps")
class GuardTests(unittest.TestCase):
    """The guard steps of pages.yml run as GitHub runs them (bash -eo pipefail, the step's env from the run's contexts,
    RUNNER_TEMP and GITHUB_REPOSITORY as the runner sets them) in a repository laid out as their checkout leaves it.
    curl is a fake that answers the Release lookup with a fabricated response, and python3 is the interpreter of these
    tests. Each refused run differs from an accepted one in one respect, so it passes if the check of that respect is
    removed or loosened."""

    @classmethod
    def setUpClass(cls):
        _, jobs = workflows.read_workflow("pages.yml")
        # A step missing from a job is left out here; WorkflowTests requires every one.
        cls.guards = {}
        for job in ("guard", "build"):
            steps = {step["name"]: step for step in jobs[job]["steps"]}
            cls.guards[job] = [
                (role, steps[name]) for role, name in GUARD_STEPS if role != "checkout" and name in steps
            ]
        cls.approve = next((step for step in jobs["guard"]["steps"] if step["name"] == APPROVE), None)
        cls.temporary = tempfile.TemporaryDirectory()
        directory = Path(cls.temporary.name)
        (directory / "checkout").mkdir()
        (directory / "no-main").mkdir()
        cls.checkout = Checkout(directory / "checkout")
        cls.without_main = Checkout(directory / "no-main", main=False)
        # The fake curl and this interpreter as python3, on the steps' PATH ahead of the system's commands.
        cls.bin = directory / "bin"
        cls.bin.mkdir()
        (directory / "curl.py").write_text(FAKE_CURL, encoding="utf-8")
        python = shlex.quote(sys.executable)
        for command, line in (
            ("curl", f'exec {python} -I {shlex.quote(str(directory / "curl.py"))} "$@"'),
            ("python3", f'exec {python} "$@"'),
        ):
            (cls.bin / command).write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
            (cls.bin / command).chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def environment(self, role, step, contexts, runner):
        env = {**runner, "LANG": "en_US.UTF-8"}  # a locale whose [0-9] also matches other scripts' digits
        for name, value in step["env"].items():
            expression = EXPRESSION.fullmatch(value)
            if expression is None:
                self.assertNotIn("${{", value, f"{name}: one property per variable")
                env[name] = workflows.yaml_scalar(value, name)
                continue
            self.assertIn(expression[1], GUARD_CONTEXTS, f"{name} reads a context these tests do not model")
            if expression[1] == "github.token":
                self.assertIn(role, TOKEN_READERS, f"{name}: only the Release lookup reads the token")
            env[name] = contexts.get(expression[1], "")
        return env

    def run_guards(self, job, contexts, response=None, head=None, checkout=None, skip=()):
        """(The role of the guard step that refused the run, or None; the output of the last step that ran; the requests
        the fake curl got; the commit the guard job approved, or None.) The commit of the contexts and head are names of
        Checkout commits; HEAD is the run's commit unless head is given. The fake curl answers with response, by default
        the published full Release of the ref's tag. The steps whose roles are in skip do not run. The guard job's
        approve step runs after its guard steps, as GitHub runs it: only if none of them failed."""
        checkout = checkout or self.checkout
        checkout.detach(head or contexts["github.sha"])
        tag = contexts["github.ref"].removeprefix("refs/tags/")
        contexts = {**contexts, "github.sha": checkout.commits[contexts["github.sha"]], "github.token": TOKEN}
        with tempfile.TemporaryDirectory() as temporary:
            runner = Path(temporary)
            (runner / "temp").mkdir()
            (runner / "response.json").write_text(json.dumps(response or release_response(tag)), encoding="utf-8")
            env = {
                **checkout.env,
                "PATH": f"{self.bin}{os.pathsep}{checkout.env['PATH']}",
                "RUNNER_TEMP": str(runner / "temp"),
                "GITHUB_REPOSITORY": REPOSITORY,
                "FAKE_RESPONSE": str(runner / "response.json"),
                "FAKE_CURL_LOG": str(runner / "requests.jsonl"),
            }
            refused, output = None, ""
            for role, step in self.guards[job]:
                if role in skip:
                    continue
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                    cwd=checkout.directory,
                    env=self.environment(role, step, contexts, env),
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                output = result.stdout + result.stderr
                if result.returncode != 0:
                    self.assertRegex(result.stdout, r"(?m)^::error::\S", "a refusal says why")
                    refused = role
                    break
                self.assertNotIn("::error::", result.stdout)
            approved = None
            if refused is None and job == "guard" and self.approve is not None:
                step, outputs = self.approve, runner / "outputs"
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                    cwd=checkout.directory,
                    env={**self.environment("approve", step, contexts, env), "GITHUB_OUTPUT": str(outputs)},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                (approved,) = re.fullmatch(r"commit=([0-9a-f]{40})\n", outputs.read_text("utf-8")).groups()
            log = runner / "requests.jsonl"
            requests = [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []
        return refused, output, requests, approved

    def assert_guards(self, refused_by, contexts, response=None, head=None, checkout=None, skip=()):
        """The guard steps of both jobs refuse the run at the step of role `refused_by`, or pass it if that is None. A
        run whose event and ref pass looks up its tag's Release with one request, whether a release or a manual run
        started it; no other run makes one."""
        run = {key.rsplit(".", 1)[-1]: value for key, value in contexts.items()}
        if response:
            run["response"] = json.dumps(response)[:100]
        if head:
            run["head"] = head
        if checkout:
            run["origin_main"] = "missing"
        if skip:
            run["skipped"] = ", ".join(skip)
        lookups = int(refused_by != "event" and "lookup" not in skip)
        tag = contexts["github.ref"].removeprefix("refs/tags/")
        url = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}"
        commit = (checkout or self.checkout).commits[contexts["github.sha"]]
        for job in self.guards:
            with self.subTest(job=job, **run):
                refused, output, requests, approved = self.run_guards(job, contexts, response, head, checkout, skip)
                self.assertEqual(refused, refused_by, output)
                if refused_by is None:
                    self.assertIn("is on main.", output)
                if job == "guard":
                    self.assertEqual(approved, None if refused_by else commit, "the commit the guard job approved")
                self.assertEqual(len(requests), lookups, "requests to the GitHub API")
                for request in requests:
                    self.assertEqual(request["urls"], [url])
                    self.assertEqual(sorted(request["headers"]), sorted(LOOKUP_HEADERS))

    def test_a_published_release_or_a_manual_run_on_its_tag_passes(self):
        # Either run passes with GitHub's answer for the published full Release of its tag, which it asks for once.
        for contexts in (
            published(),
            published("v1.2.0", "tip"),
            published("v10.20.30"),
            dispatched(),
            dispatched("refs/tags/v1.2.0", "tip"),
            dispatched("refs/tags/v10.20.30", "released"),
            # A zero on its own is a number, and so is a number that ends in zeros.
            dispatched("refs/tags/v0.0.0"),
            dispatched("refs/tags/v1.10.0"),
        ):
            self.assert_guards(None, contexts)

    def test_other_events_are_refused_even_on_a_release_tag(self):
        for event in ("push", "pull_request", "pull_request_target", "schedule", "workflow_run", "repository_dispatch"):
            self.assert_guards("event", dispatched(event=event))
        self.assert_guards("event", dispatched("refs/heads/main", "tip", event="push"))
        self.assert_guards("event", dispatched("refs/pull/7/merge", "aside", event="pull_request"))
        self.assert_guards("event", dispatched("refs/heads/main", "tip", event="schedule"))

    def test_a_ref_that_is_not_a_vXYZ_tag_is_refused(self):
        for ref in (
            "refs/heads/main",
            "refs/heads/v1.1.0",
            "refs/heads/refs/tags/v1.1.0",
            "refs/tags/v1.1",
            "refs/tags/1.1.0",
            "refs/tags/v1.1.0-rc1",
            "refs/tags/v1.1.0.1",
            "refs/tags/V1.1.0",
            "refs/tags/xv1.1.0",
            "refs/tags/v1.1.0/x",
            "refs/tags/v1..0",
            "refs/tags/v1.1.0 ",
            "refs/tags/v1.1.0\nrefs/tags/v1.1.0",
            "refs/tags/v1.1.\u0663",  # ARABIC-INDIC DIGIT THREE, a [0-9] in en_US.UTF-8
            # A leading zero, in each of the three numbers.
            "refs/tags/v01.1.0",
            "refs/tags/v1.01.0",
            "refs/tags/v1.1.00",
            "refs/tags/v00.0.0",
            # Another character than a dot between two numbers, in each of the two places: a pattern whose dot is not
            # escaped there accepts them.
            "refs/tags/v1-1.0",
            "refs/tags/v1.1-0",
            "refs/tags/v1x1.0",
            "refs/tags/v1.1x0",
            "refs/tags/v1111.0",
            "refs/tags/v1.111",
            "",
        ):
            self.assert_guards("event", dispatched(ref))
        for tag in ("v1.1", "1.1.0", "v1.1.0-rc1", "v01.1.0", "v1.1.01", "v1-1.0", "v1.1-0", "v1x1.0", "v1.1x0"):
            self.assert_guards("event", published(tag))

    def test_a_prerelease_or_another_release_action_is_refused(self):
        # The release event's payload alone refuses these, before the lookup makes any request.
        for prerelease in ("true", ""):
            self.assert_guards("event", published(**{"github.event.release.prerelease": prerelease}))
        for action in ("created", "released", "prereleased", "edited", ""):
            self.assert_guards("event", published(**{"github.event.action": action}))
        self.assert_guards("event", published(**{"github.event.release.tag_name": "v1.2.0"}))

    def test_a_release_run_is_judged_by_its_release_as_it_is_now(self):
        # Re-running a run keeps the event it started with, so the payload of a re-run release run still says published
        # and not a prerelease after the Release was made a prerelease or a draft, or deleted (GitHub then answers 404).
        # The guards of each job look the Release up once, as for a manual run, and the answer refuses such a run.
        for response in (
            release_response(prerelease=True),
            release_response(draft=True),
            NOT_FOUND,
            release_response(tag_name="v1.2.0"),
            release_response(status="500"),
            answer("200", "<!DOCTYPE html>"),
        ):
            self.assert_guards("release", published(), response)
        self.assert_guards("lookup", published(), UNREACHABLE)
        self.assert_guards("release", published(), skip=("lookup",))

    def test_the_lookup_is_one_get_with_the_job_token_on_stdin(self):
        for contexts in runs("v1.2.0", "tip"):
            for job in self.guards:
                with self.subTest(job=job, event=contexts["github.event_name"]):
                    refused, output, requests, _ = self.run_guards(job, contexts)
                    self.assertIsNone(refused, output)
                    (request,) = requests
                    self.assertEqual(
                        request["urls"], [f"https://api.github.com/repos/{REPOSITORY}/releases/tags/v1.2.0"]
                    )
                    # The fake curl refuses every option that could send anything but a GET or follow a redirect.
                    self.assertEqual(request["headers"], LOOKUP_HEADERS)
                    self.assertIn("@-", request["options"]["--header"], "the token comes on stdin")
                    self.assertNotIn(TOKEN, " ".join(request["argv"]), "no command line holds the token")
                    self.assertEqual(request["options"]["--proto"], ["=https"])
                    # A request that hangs fails the step with its own error before the job's timeout cancels the job.
                    max_time = request["options"]["--max-time"]
                    self.assertEqual(len(max_time), 1, "one --max-time")
                    self.assertRegex(max_time[0], r"^[1-9][0-9]*$", "a positive number of whole seconds")
                    self.assertLessEqual(int(max_time[0]), LOOKUP_MAX_SECONDS)

    def test_a_run_on_a_tag_without_a_published_release_is_refused(self):
        # GitHub answers 404 for a tag that has no Release and for one whose Release is still a draft.
        for contexts in (*runs(), *runs("v1.2.0", "tip")):
            self.assert_guards("release", contexts, NOT_FOUND)

    def test_a_run_on_a_prerelease_or_a_draft_is_refused(self):
        # Only the JSON value false passes: not true, null, a missing field, a string or a number.
        for contexts in runs():
            for field in ("prerelease", "draft"):
                for value in (True, None, MISSING, "false", 0):
                    self.assert_guards("release", contexts, release_response(**{field: value}))

    def test_a_run_on_the_release_of_another_tag_is_refused(self):
        for contexts in runs():
            for tag_name in ("v1.2.0", "V1.1.0", "v1.1.0 ", "refs/tags/v1.1.0", "", None, MISSING, ["v1.1.0"]):
                self.assert_guards("release", contexts, release_response(tag_name=tag_name))

    def test_a_lookup_answered_with_another_status_than_200_is_refused(self):
        # Each body is the published full Release of the tag, so the status alone refuses the run.
        for contexts in runs():
            for status in ("201", "203", "301", "304", "401", "403", "429", "500", "502", "2000", " 200", ""):
                self.assert_guards("release", contexts, release_response(status=status))

    def test_a_lookup_answer_that_is_not_a_json_object_is_refused(self):
        release = release_response()["body"]
        for contexts in runs():
            for body in ("", "Not Found", "<!DOCTYPE html>", release[:-1], f"{release} {{}}", f"[{release}]", "null"):
                self.assert_guards("release", contexts, answer("200", body))

    def test_a_missing_lookup_answer_is_refused(self):
        # The check reads the answer the lookup left under RUNNER_TEMP; without one, it refuses the run.
        for contexts in runs():
            self.assert_guards("release", contexts, skip=("lookup",))

    def test_a_lookup_that_gets_no_answer_is_refused(self):
        for contexts in runs():
            self.assert_guards("lookup", contexts, UNREACHABLE)

    def test_a_commit_that_is_not_on_main_is_refused(self):
        self.assert_guards("main", published("v1.3.0", "aside"))
        self.assert_guards("main", dispatched("refs/tags/v1.3.0", "aside"))
        # The checkout is not the run's commit: the run's commit is on main and the checkout is not, or the reverse.
        self.assert_guards("main", dispatched(), head="aside")
        self.assert_guards("main", dispatched("refs/tags/v1.3.0", "aside"), head="released")
        self.assert_guards("main", published(), checkout=self.without_main)


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
