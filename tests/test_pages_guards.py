"""Run the guard steps of pages.yml as GitHub runs them: only a run on a vX.Y.Z tag whose commit is on main and that has
a published GitHub Release, not marked as a prerelease, passes them, and only its commit is approved for the deploy job.

The steps run under bash -eo pipefail with their env from the run's contexts, in Git repositories laid out as their
checkout leaves them; a fake curl answers the Release lookup. tests/test_pages.py pins the steps' names and order in
both jobs (GUARD_STEPS, APPROVE) and the deploy job's condition, which these tests evaluate with the outputs that the
guard job's last step records.
"""

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

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The workflow reader of the tooling tests (see tests/test_pages.py), and the guard steps of both jobs by role, the
# guard job's last step and the expression evaluator of the workflow tests in tests/test_pages.py.
workflows = load("test_tooling", "tests/test_tooling.py")
structure = load("test_pages", "tests/test_pages.py")
GUARD_STEPS, APPROVE = structure.GUARD_STEPS, structure.APPROVE
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
# A guard job output, which gives an output of its approve step; and what that step records in GITHUB_OUTPUT.
APPROVED_OUTPUT = re.compile(r"\$\{\{ steps\.approve\.outputs\.([a-z_]+) \}\}")
RECORDED = re.compile(r"commit=(?P<commit>[0-9a-f]{40})\nattempt=(?P<attempt>[1-9][0-9]*)\n")
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


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git to run the guard steps")
class GuardTests(unittest.TestCase):
    """The guard steps of pages.yml run as GitHub runs them (bash -eo pipefail, the step's env from the run's contexts,
    RUNNER_TEMP, GITHUB_REPOSITORY and GITHUB_RUN_ATTEMPT as the runner sets them) in a repository laid out as their
    checkout leaves it. curl is a fake that answers the Release lookup with a fabricated response, and python3 is the
    interpreter of these tests. Each refused run differs from an accepted one in one respect, so it passes if the check
    of that respect is removed or loosened."""

    @classmethod
    def setUpClass(cls):
        _, jobs = workflows.read_workflow("pages.yml")
        # A step missing from a job is left out here; WorkflowTests in tests/test_pages.py requires every one.
        cls.guards = {}
        for job in ("guard", "build"):
            steps = {step["name"]: step for step in jobs[job]["steps"]}
            cls.guards[job] = [
                (role, steps[name]) for role, name in GUARD_STEPS if role != "checkout" and name in steps
            ]
        cls.approve = next((step for step in jobs["guard"]["steps"] if step["name"] == APPROVE), None)
        outputs = workflows.mapping(jobs["guard"]["keys"].get("outputs", ("", []))[1], 6, "outputs")
        cls.outputs = {name: value for name, (value, _) in outputs.items()}
        cls.condition = workflows.block_text(*jobs["deploy"]["keys"].get("if", ("", [])))
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

    def run_step(self, role, step, contexts, runner, directory, **variables):
        """The result of step, run as GitHub runs it: bash -eo pipefail in directory, with the runner's variables, the
        step's env from contexts and then variables; its output decoded as UTF-8."""
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            cwd=directory,
            env={**self.environment(role, step, contexts, runner), **variables},
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def run_approve(self, contexts, runner, directory, outputs):
        """What the guard job's approve step records in the file outputs: {"commit": ..., "attempt": ...}."""
        result = self.run_step("approve", self.approve, contexts, runner, directory, GITHUB_OUTPUT=str(outputs))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        recorded = RECORDED.fullmatch(outputs.read_text("utf-8"))
        self.assertIsNotNone(recorded, "the commit and the attempt, once each")
        return recorded.groupdict()

    def run_guards(self, job, contexts, response=None, head=None, checkout=None, skip=(), attempt="1"):
        """(The role of the guard step that refused the run, or None; the output of the last step that ran; the requests
        the fake curl got; what the guard job approved, or None.) The commit of the contexts and head are names of
        Checkout commits; HEAD is the run's commit unless head is given. The fake curl answers with response, by default
        the published full Release of the ref's tag. The steps whose roles are in skip do not run; the run's attempt
        (GITHUB_RUN_ATTEMPT) is attempt. The guard job's approve step runs after its guard steps, as GitHub runs it:
        only if none of them failed."""
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
                "GITHUB_RUN_ATTEMPT": attempt,
                "FAKE_RESPONSE": str(runner / "response.json"),
                "FAKE_CURL_LOG": str(runner / "requests.jsonl"),
            }
            refused, output = None, ""
            for role, step in self.guards[job]:
                if role in skip:
                    continue
                result = self.run_step(role, step, contexts, env, checkout.directory)
                output = result.stdout + result.stderr
                if result.returncode != 0:
                    self.assertRegex(result.stdout, r"(?m)^::error::\S", "a refusal says why")
                    refused = role
                    break
                self.assertNotIn("::error::", result.stdout)
            approved = None
            if refused is None and job == "guard" and self.approve is not None:
                approved = self.run_approve(contexts, env, checkout.directory, runner / "outputs")
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
                    approved_by = None if refused_by else {"commit": commit, "attempt": "1"}
                    self.assertEqual(approved, approved_by, "the commit the guard job approved, and the attempt")
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

    def deployment(self, contexts, approved, run_attempt):
        """The deploy job's contexts in the attempt run_attempt of a run with contexts, whose guard job recorded
        approved: the guard job's outputs as its outputs: mapping gives them, and the prerelease flag of a release
        event's payload as the JSON value it is there."""
        needs = {}
        for name, value in self.outputs.items():
            source = APPROVED_OUTPUT.fullmatch(value)
            self.assertIsNotNone(source, f"the guard job's output {name} is an output of its approve step")
            needs[f"needs.guard.outputs.{name}"] = approved.get(source[1])
        payload = {"github.event.release.prerelease": False} if contexts["github.event_name"] == "release" else {}
        commit = self.checkout.commits[contexts["github.sha"]]
        return {**contexts, **payload, "github.sha": commit, "github.run_attempt": run_attempt, **needs}

    def test_the_deploy_job_runs_only_in_the_attempt_whose_guard_job_approved_the_commit(self):
        # The approve step records the run's attempt with the commit, and the guard job gives both as its outputs. The
        # deploy job's condition then holds in that attempt and not in a later one: a re-run of the deploy job alone
        # keeps the guard job's outputs of the attempt it repeats, and looks up no Release.
        for contexts in runs("v1.2.0", "tip"):
            for attempt in ("1", "2", "10"):
                with self.subTest(event=contexts["github.event_name"], attempt=attempt):
                    refused, output, _, approved = self.run_guards("guard", contexts, attempt=attempt)
                    self.assertIsNone(refused, output)
                    self.assertEqual(approved, {"commit": self.checkout.commits["tip"], "attempt": attempt})
                    for deploy_attempt in (attempt, str(int(attempt) + 1)):
                        deployment = self.deployment(contexts, approved, deploy_attempt)
                        deploys = structure.truthy(structure.evaluate(self.condition, deployment))
                        self.assertIs(deploys, deploy_attempt == attempt, f"the deploy job in attempt {deploy_attempt}")

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


if __name__ == "__main__":
    unittest.main()
