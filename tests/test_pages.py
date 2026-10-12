"""Ensure only a release is deployed, and only tracked, allowlisted website files reach the GitHub Pages payload."""

import importlib.util
import os
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
# toJSON() turns false into 'false' and a missing flag into 'null'.
DEPLOY_CONDITION = (
    "startsWith(github.ref, 'refs/tags/v') && (github.event_name == 'workflow_dispatch' || "
    "(github.event_name == 'release' && github.event.action == 'published' && "
    "toJSON(github.event.release.prerelease) == 'false'))"
)
# The only contexts the guard steps may read. GitHub renders a property the event does not have as an empty string.
GUARD_CONTEXTS = (
    "github.event_name",
    "github.event.action",
    "github.ref",
    "github.event.release.tag_name",
    "github.event.release.prerelease",
    "github.sha",
)
EXPRESSION = re.compile(r"\$\{\{ ([a-z_.]+) \}\}")
COMMITTER = {"NAME": "Release test", "EMAIL": "release@example.invalid", "DATE": "2026-10-11T00:00:00Z"}
VERSION = r"v[0-9]+\.[0-9]+\.[0-9]+"
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
        # The guard job is the guards alone: the event and ref check, a checkout with every commit, the main check. The
        # build job starts with the same three steps, so nothing it runs from the checkout precedes them.
        guard, build = self.jobs["guard"]["steps"], self.jobs["build"]["steps"]
        self.assertEqual(len(guard), 3)
        self.assertEqual(guard[0]["lines"], build[0]["lines"])
        self.assertEqual(guard[2]["lines"], build[2]["lines"])
        for job, steps in (("guard", guard), ("build", build)):
            with self.subTest(job=job):
                first, checkout, main = steps[:3]
                for step in (first, main):
                    self.assertIsNotNone(step["run"])
                    self.assertEqual(set(step["keys"]) - {"name", "env", "run"}, set(), "no if: or continue-on-error")
                self.assertTrue(checkout["keys"]["uses"].startswith("actions/checkout@"))
                self.assertIn("          fetch-depth: 0", checkout["lines"])
                self.assertIn("          persist-credentials: false", checkout["lines"])
        later = [step["keys"].get("uses") or step["run"] for step in build[3:]]
        self.assertEqual(
            later[:2],
            [
                "python3 -I tests/test_pages.py",
                'python3 -I scripts/prepare_pages.py --output "$RUNNER_TEMP/deemo-site"',
            ],
        )
        self.assertTrue(later[2].startswith("actions/upload-pages-artifact@"))
        self.assertIn("          path: ${{ runner.temp }}/deemo-site", build[5]["lines"])
        self.assertEqual(len(later), 3)

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
    """The guard steps of pages.yml run as GitHub runs them (bash -eo pipefail, the step's env from the run's contexts)
    in a repository laid out as their checkout leaves it. Each refused run differs from an accepted one in one respect,
    so it passes if the check of that respect is removed or loosened."""

    @classmethod
    def setUpClass(cls):
        _, jobs = workflows.read_workflow("pages.yml")
        cls.guards = {job: (jobs[job]["steps"][0], jobs[job]["steps"][2]) for job in ("guard", "build")}
        cls.temporary = tempfile.TemporaryDirectory()
        directory = Path(cls.temporary.name)
        (directory / "checkout").mkdir()
        (directory / "no-main").mkdir()
        cls.checkout = Checkout(directory / "checkout")
        cls.without_main = Checkout(directory / "no-main", main=False)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def environment(self, step, contexts, checkout):
        env = {**checkout.env, "LANG": "en_US.UTF-8"}  # a locale whose [0-9] also matches other scripts' digits
        for name, value in step["env"].items():
            expression = EXPRESSION.fullmatch(value)
            if expression is None:
                self.assertNotIn("${{", value, f"{name}: one property per variable")
                env[name] = workflows.yaml_scalar(value, name)
                continue
            self.assertIn(expression[1], GUARD_CONTEXTS, f"{name} reads a context these tests do not model")
            env[name] = contexts.get(expression[1], "")
        return env

    def run_guards(self, job, contexts, head=None, checkout=None):
        """(1 or 2 for the guard step that refused the run, or None, and the output of the last step that ran). The
        commit of the contexts and head are names of Checkout commits; HEAD is the run's commit unless head is given."""
        checkout = checkout or self.checkout
        checkout.detach(head or contexts["github.sha"])
        contexts = {**contexts, "github.sha": checkout.commits[contexts["github.sha"]]}
        for number, step in enumerate(self.guards[job], 1):
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                cwd=checkout.directory,
                env=self.environment(step, contexts, checkout),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                self.assertRegex(result.stdout, r"(?m)^::error::\S", "a refusal says why")
                return number, result.stdout + result.stderr
            self.assertNotIn("::error::", result.stdout)
        return None, result.stdout + result.stderr

    def assert_guards(self, refused_by, contexts, head=None, checkout=None):
        """The guard steps of both jobs refuse the run at step `refused_by` (1 or 2), or pass it if that is None."""
        run = {key.rsplit(".", 1)[-1]: value for key, value in contexts.items()}
        if head:
            run["head"] = head
        if checkout:
            run["origin_main"] = "missing"
        for job in self.guards:
            with self.subTest(job=job, **run):
                refused, output = self.run_guards(job, contexts, head, checkout)
                self.assertEqual(refused, refused_by, output)
                if refused_by is None:
                    self.assertIn("is on main.", output)

    def test_a_published_release_or_a_manual_run_on_its_tag_passes(self):
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
            self.assert_guards(1, dispatched(event=event))
        self.assert_guards(1, dispatched("refs/heads/main", "tip", event="push"))
        self.assert_guards(1, dispatched("refs/pull/7/merge", "aside", event="pull_request"))
        self.assert_guards(1, dispatched("refs/heads/main", "tip", event="schedule"))

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
            "",
        ):
            self.assert_guards(1, dispatched(ref))
        for tag in ("v1.1", "1.1.0", "v1.1.0-rc1", "v01.1.0", "v1.1.01"):
            self.assert_guards(1, published(tag))

    def test_a_prerelease_or_another_release_action_is_refused(self):
        for prerelease in ("true", ""):
            self.assert_guards(1, published(**{"github.event.release.prerelease": prerelease}))
        for action in ("created", "released", "prereleased", "edited", ""):
            self.assert_guards(1, published(**{"github.event.action": action}))
        self.assert_guards(1, published(**{"github.event.release.tag_name": "v1.2.0"}))

    def test_a_commit_that_is_not_on_main_is_refused(self):
        self.assert_guards(2, published("v1.3.0", "aside"))
        self.assert_guards(2, dispatched("refs/tags/v1.3.0", "aside"))
        # The checkout is not the run's commit: the run's commit is on main and the checkout is not, or the reverse.
        self.assert_guards(2, dispatched(), head="aside")
        self.assert_guards(2, dispatched("refs/tags/v1.3.0", "aside"), head="released")
        self.assert_guards(2, published(), checkout=self.without_main)


class DocumentationTests(unittest.TestCase):
    def section(self, readme):
        text = (ROOT / readme).read_text(encoding="utf-8")
        return text[text.index(DEPLOYMENT_SECTIONS[readme]) :].split("\n## ", 1)[0]

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
