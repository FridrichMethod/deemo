"""Ensure only a release is deployed, and only tracked, allowlisted website files reach the GitHub Pages payload.

tests/test_pages_guards.py runs the guard steps whose names and order these tests pin, tests/test_pages_docs.py checks
that the READMEs and the header comment of pages.yml state the rules these tests pin, and the jobs' conditions are read
with the expression evaluator of tests/test_pages_expressions.py.
"""

import importlib.util
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
# The reader of the expressions in the jobs' conditions, which reads them as GitHub does.
expressions = load("test_pages_expressions", "tests/test_pages_expressions.py")
evaluate, truthy = expressions.evaluate, expressions.truthy

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
# The job that fails an attempt that did not run the guard job, as the Actions page names it, and its condition and its
# one step, verbatim. The deploy job skips such an attempt (RE_RUNS), which would then end green with nothing deployed.
# GitHub re-runs a job together with every job that needs it, so a re-run of the deploy job, or of any job before it but
# the guard job, runs this job too. always() makes GitHub evaluate its condition whatever the jobs it needs did, and
# the condition holds when the guard job succeeded and its attempt output is not the run's attempt: only when the
# attempt kept the guard job's outputs of an earlier one (RERUN_CHECKS, RUNS).
RERUN_CHECK, RERUN_CHECK_NAME = "rerun-check", "Re-run check"
RERUN_CONDITION = "always() && needs.guard.result == 'success' && needs.guard.outputs.attempt != github.run_attempt"
RERUN_ERROR = (
    "::error::This attempt did not run the Release guard job, so it deployed nothing. Re-run all jobs to deploy the"
    " release."
)
RERUN_STEP = ("Refuse an attempt that did not run the Release guard job", f'echo "{RERUN_ERROR}"\nexit 1\n')
# The jobs of pages.yml in the order they run. Each needs every job before it, so a job's needs are all the jobs it
# depends on, directly or not.
JOBS = ("guard", "test", "build", "deploy", RERUN_CHECK)


def checking(run_attempt, result, **outputs):
    """The re-run check job's contexts in the attempt run_attempt of a release's run, with the guard job's result and
    outputs as it sees them."""
    return {**deployment(run_attempt, **outputs), "needs.guard.result": result}


# Attempts of a run and whether the re-run check job's condition holds in them. It does not in an attempt that ran the
# guard job, nor after a guard job that refused the run or was cancelled; it does in an attempt that kept the guard
# job's outputs of an earlier one, and if those outputs ever lacked the attempt. GitHub's run_attempt is a string; as a
# number it would compare the same.
RERUN_CHECKS = {
    "the first attempt": (checking("1", "success", commit=COMMIT, attempt="1"), False),
    "a re-run of all jobs": (checking("2", "success", commit=COMMIT, attempt="2"), False),
    "attempt 10, a re-run of all jobs": (checking("10", "success", commit=COMMIT, attempt="10"), False),
    "a re-run that kept the guard job of attempt 1": (checking("2", "success", commit=COMMIT, attempt="1"), True),
    "attempt 10 with the guard job of attempt 1": (checking("10", "success", commit=COMMIT, attempt="1"), True),
    "attempt 3 with the guard job of attempt 2": (checking("3", "success", commit=COMMIT, attempt="2"), True),
    "a run the guard job refused": (checking("1", "failure"), False),
    "a re-run of all jobs the guard job refused": (checking("2", "failure"), False),
    "a re-run that kept a guard job that refused the run": (checking("2", "failure"), False),
    "a cancelled guard job": (checking("1", "cancelled"), False),
    "a guard job that succeeded without the attempt output": (checking("2", "success", commit=COMMIT), True),
    "the first attempt, its number as a number": (checking(1, "success", commit=COMMIT, attempt="1"), False),
    "a re-run that kept the guard job of attempt 1, its number as a number": (
        checking(2, "success", commit=COMMIT, attempt="1"),
        True,
    ),
}
# A condition that calls none of these runs its job only if every job it needs succeeded (GitHub adds success()).
STATUS_FUNCTION = re.compile(r"\b(?:always|success|failure|cancelled)\s*\(")


def run_attempt(jobs, number, event, previous=None, again=(), failing=()):
    """({job id: (result, outputs)}, [the jobs that ran]) for attempt `number` of a run of pages.yml on v1.1.2 that
    event started, as GitHub runs it. jobs maps each job id to (its needs, its condition), in the order they run.

    A first attempt (previous None) runs every job. A re-run runs again the jobs in `again` and every job that needs one
    of them, and keeps the result and the outputs of every other job of `previous`, the attempt it repeats. A job that
    runs is skipped if its condition does not hold, or calls no status function and a job it needs did not succeed.
    Otherwise it fails if it is in `failing`, or is the re-run check job, whose one step exits 1 (RERUN_STEP), and
    succeeds if not; the guard job then records the run's commit and the attempt, as its approve step does."""
    again, results, ran = set(jobs if previous is None else again), {}, []
    for job_id, (needs, condition) in jobs.items():
        if again & set(needs):
            again.add(job_id)
        if job_id not in again:
            results[job_id] = previous[job_id]
            continue
        contexts = deployment(str(number), event)
        for need in needs:
            result, outputs = results[need]
            contexts[f"needs.{need}.result"] = result
            contexts.update({f"needs.{need}.outputs.{name}": value for name, value in outputs.items()})
        ready = STATUS_FUNCTION.search(condition) or all(results[need][0] == "success" for need in needs)
        if not (ready and truthy(evaluate(condition or "true", contexts))):
            results[job_id] = ("skipped", {})
        elif job_id in failing or job_id == RERUN_CHECK:
            ran.append(job_id)
            results[job_id] = ("failure", {})
        else:
            ran.append(job_id)
            results[job_id] = ("success", {"commit": COMMIT, "attempt": str(number)} if job_id == "guard" else {})
    return results, ran


# Runs as a list of attempts, each (what it re-runs, the jobs that fail in it), and whether the last attempt deployed
# and whether it ran the re-run check job. A first attempt re-runs nothing (None); "all" is "Re-run all jobs", "failed"
# "Re-run failed jobs" (the jobs that failed in the attempt it repeats), and a job id "Re-run this job" on that job. A
# run that only the guard job's record of an earlier attempt kept from deploying fails in the re-run check job; a run
# that the guard job refused fails in the guard job and does not run the re-run check job.
FIRST = (None, ())
RUNS = {
    "the first attempt of a release's run": ("release", [FIRST], (True, False)),
    "the first attempt of a manual run": ("workflow_dispatch", [FIRST], (True, False)),
    "a re-run of all jobs": ("release", [FIRST, ("all", ())], (True, False)),
    "a re-run of all jobs of a manual run": ("workflow_dispatch", [FIRST, ("all", ())], (True, False)),
    "a re-run of all jobs after the deploy job failed": ("release", [(None, ("deploy",)), ("all", ())], (True, False)),
    "a re-run of the guard job, which re-runs every job": ("release", [FIRST, ("guard", ())], (True, False)),
    "a re-run of the failed jobs after the guard job failed": (
        "release",
        [(None, ("guard",)), ("failed", ())],
        (True, False),
    ),
    "a re-run of the deploy job alone": ("release", [FIRST, ("deploy", ())], (False, True)),
    "a re-run of the deploy job alone, in a manual run": ("workflow_dispatch", [FIRST, ("deploy", ())], (False, True)),
    "a re-run of the failed jobs after the deploy job failed": (
        "release",
        [(None, ("deploy",)), ("failed", ())],
        (False, True),
    ),
    "a re-run of the failed jobs after the test job failed": (
        "release",
        [(None, ("test",)), ("failed", ())],
        (False, True),
    ),
    "a re-run of the failed jobs after the build job failed": (
        "release",
        [(None, ("build",)), ("failed", ())],
        (False, True),
    ),
    "a re-run of the failed jobs in which the test job fails again": (
        "release",
        [(None, ("test",)), ("failed", ("test",))],
        (False, True),
    ),
    "a re-run of the test job alone": ("release", [FIRST, ("test", ())], (False, True)),
    "a re-run of the build job alone": ("release", [FIRST, ("build", ())], (False, True)),
    "a re-run of the deploy job alone after a re-run of all jobs": (
        "release",
        [FIRST, ("all", ()), ("deploy", ())],
        (False, True),
    ),
    "a re-run of the failed jobs after a re-run of the deploy job alone": (
        "release",
        [FIRST, ("deploy", ()), ("failed", ())],
        (False, True),
    ),
    "a re-run of all jobs after a re-run of the deploy job alone": (
        "release",
        [FIRST, ("deploy", ()), ("all", ())],
        (True, False),
    ),
    "a run the guard job refused": ("release", [(None, ("guard",))], (False, False)),
    "a manual run the guard job refused": ("workflow_dispatch", [(None, ("guard",))], (False, False)),
    "a re-run of all jobs the guard job refuses again": (
        "release",
        [(None, ("guard",)), ("all", ("guard",))],
        (False, False),
    ),
    "a re-run of the failed jobs the guard job refuses again": (
        "release",
        [(None, ("guard",)), ("failed", ("guard",))],
        (False, False),
    ),
    "a re-run of the deploy job alone after the guard job refused the run": (
        "release",
        [(None, ("guard",)), ("deploy", ())],
        (False, False),
    ),
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

    def test_the_re_run_check_job_only_fails_the_attempt(self):
        # The jobs in the order they run, each needing every job before it; the re-run check job last, with no
        # permissions, no checkout and no action: one step that prints its error and exits 1 (run under bash in
        # tests/test_pages_guards.py). No environment, outputs, strategy or continue-on-error.
        self.assertEqual(list(self.jobs), list(JOBS))
        for index, job_id in enumerate(JOBS[1:], 1):
            with self.subTest(job=job_id):
                needs = workflows.yaml_scalar(self.jobs[job_id]["keys"]["needs"][0], "needs")
                self.assertEqual(needs if isinstance(needs, list) else [needs], list(JOBS[:index]))
        job = self.jobs[RERUN_CHECK]
        self.assertEqual(
            set(job["keys"]), {"name", "needs", "if", "runs-on", "timeout-minutes", "permissions", "steps"}
        )
        self.assertEqual(workflows.yaml_scalar(job["keys"]["name"][0], "name"), RERUN_CHECK_NAME)
        self.assertEqual(workflows.block_text(*job["keys"]["if"]), RERUN_CONDITION)
        self.assertEqual(workflows.job_permissions(job), "{}")
        self.assertEqual(job["keys"]["runs-on"][0], "ubuntu-latest")
        self.assertEqual(job["keys"]["timeout-minutes"][0], "1")
        (step,) = job["steps"]
        self.assertEqual(set(step["keys"]), {"name", "run"}, "no uses:, env:, if: or shell")
        self.assertEqual((step["name"], step["run"]), RERUN_STEP)

    def test_the_re_run_check_job_runs_only_in_an_attempt_that_kept_the_guard_jobs_record(self):
        condition = workflows.block_text(*self.jobs[RERUN_CHECK]["keys"]["if"])
        for scenario, (contexts, checks) in RERUN_CHECKS.items():
            with self.subTest(scenario):
                self.assertIs(truthy(evaluate(condition, contexts)), checks)

    def test_a_re_run_that_leaves_out_the_guard_job_fails_and_deploys_nothing(self):
        # The jobs of pages.yml run as GitHub runs them (run_attempt()) in first attempts and re-runs (RUNS): an attempt
        # that ran the guard job deploys unless a job fails or the guard job refuses the run, and skips the re-run check
        # job; an attempt that kept the guard job's record of an earlier one deploys nothing and fails in the re-run
        # check job. So no attempt ends green without deploying.
        def condition(job):
            return workflows.block_text(*job["keys"]["if"]) if "if" in job["keys"] else ""

        def needs(job):
            value = workflows.yaml_scalar(job["keys"].get("needs", ("",))[0], "needs")
            return value if isinstance(value, list) else [value] if value else []

        jobs = {job_id: (needs(job), condition(job)) for job_id, job in self.jobs.items()}
        for scenario, (event, attempts, (deploys, checks)) in RUNS.items():
            with self.subTest(scenario):
                results = None
                for number, (rerun, failing) in enumerate(attempts, 1):
                    if rerun is None:
                        again = set()  # a first attempt runs every job
                    elif rerun == "failed":
                        again = {job_id for job_id, (result, _) in results.items() if result == "failure"}
                    else:
                        again = set(jobs) if rerun == "all" else {rerun}
                    results, ran = run_attempt(jobs, number, event, results, again, failing)
                deployed = "deploy" in ran and results["deploy"][0] == "success"
                green = all(result != "failure" for result, _ in results.values())
                self.assertIs(green, deployed, "an attempt ends green only if it deployed")
                self.assertEqual((deployed, RERUN_CHECK in ran), (deploys, checks))
                if checks:
                    self.assertEqual(results[RERUN_CHECK][0], "failure")

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
