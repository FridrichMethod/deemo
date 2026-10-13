"""Ensure the READMEs and the header comment of pages.yml state the release rules that pages.yml enforces, and none of
the rules it no longer has.

tests/test_pages.py pins the workflow these documents describe, the names of its jobs included.
"""

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pages = load("prepare_pages", "scripts/prepare_pages.py")
# The workflow reader of the tooling tests (see tests/test_pages.py), and the re-run check job's id and its name on the
# Actions page, which the workflow tests in tests/test_pages.py pin.
workflows = load("test_tooling", "tests/test_tooling.py")
structure = load("test_pages", "tests/test_pages.py")
RERUN_CHECK, RERUN_CHECK_NAME = structure.RERUN_CHECK, structure.RERUN_CHECK_NAME

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
# The first tag whose pages.yml has every check the documents describe: the Release lookup (from v1.1.1 on), the guard
# job's record of the current attempt and the re-run check job (from v1.1.2 on). A run takes pages.yml from the tag's
# commit, so a run on an older tag runs a workflow without some of them; the github-pages environment accepts
# deployments only from this tag on (2026-10-13), and an example of a manual run names no older tag.
FIRST_CHECKED_TAG = "v1.1.2"
MANUAL_RUN = re.compile(rf"gh workflow run pages\.yml --ref ({VERSION})")
# What each README's deployment section says about the release rules of 2026-10-13: what every run checks, a re-run of
# a release's run included, that a re-run without the guard job (jobs named as on the Actions page) deploys nothing and
# why, and fails with the re-run check job's error, the tag form, and the two repository settings that limit which tags
# can deploy.
GUARD_JOB = "Release guard"
RELEASE_RULES = {
    "README.md": (
        "Every run, whether a release or a manual run started it, first checks",
        "one read-only request to the GitHub API",
        "A re-run keeps the event of the run it repeats",
        "also refuses a re-run of a release's run",
        f"A re-run that leaves out the {GUARD_JOB} job, such as a re-run of the deploy job alone or of the failed jobs"
        " after the test, build or deploy job failed, deploys nothing, because the deploy job requires the guard job's"
        " record of the current attempt; re-run the whole run.",
        f"Such a re-run fails: its {RERUN_CHECK_NAME} job ends with an error that says the attempt did not run the"
        f" {GUARD_JOB} job and deployed nothing, and that all jobs must be re-run.",
        "a run on a tag without such a Release",
        "three numbers without leading zeros",
        "`v01.1.0`",
        'A repository ruleset ("Release tags v*"',
        "lets only repository admins create, move or delete `v*` tags",
        f"the rules of the `github-pages` environment accept deployments only from tags from `{FIRST_CHECKED_TAG}` on",
        "these rules keep anyone but an admin from deploying through the `github-pages` environment with a workflow of"
        " their own",
    ),
    "README.zh-CN.md": (
        "每次运行，无论由发布版还是手动触发，都会先",
        "向 GitHub API 发出一次只读请求",
        "重新运行（re-run）沿用被重新运行的那次运行的事件",
        "也会被这项检查拒绝",
        f"不包括 {GUARD_JOB} 任务的重新运行（例如只重新运行部署任务，或在测试、构建或部署任务失败后重新运行失败的任务）"
        f"不会部署任何内容，因为部署任务要求有 {GUARD_JOB} 任务对当前这次尝试（attempt）的记录；应重新运行整次运行。",
        f"这样的重新运行会失败：其 {RERUN_CHECK_NAME} 任务以错误结束，说明这次尝试没有运行 {GUARD_JOB} 任务、"
        "没有部署任何内容，必须重新运行全部任务。",
        "在没有这样的 Release 的标签上的运行",
        "三个不带前导零的数字",
        "`v01.1.0`",
        '仓库规则集（ruleset）"Release tags v*"',
        "只允许仓库管理员创建、移动或删除 `v*` 标签",
        f"`github-pages` 环境的部署规则只接受 `{FIRST_CHECKED_TAG}` 及之后的标签",
        "这些规则使管理员以外的人无法用自己的工作流经 `github-pages` 环境部署",
    ),
}
# What the READMEs said while a manual run did not look up the tag's Release.
NO_RELEASE_LOOKUP = {
    "README.md": ("does not look up the tag's GitHub Release", "use it only to redeploy a released tag"),
    "README.zh-CN.md": ("不会查询该标签的 GitHub Release", "只用它重新部署已发布的标签"),
}
# What each README's deployment section and the header comment of pages.yml say about a run on an older tag.
OLDER_TAGS = {
    "README.md": (
        f"A run on a tag older than `{FIRST_CHECKED_TAG}` would use that tag's own workflow, which lacks some of the"
        " checks described above, and the environment refuses its deployment.",
    ),
    "README.zh-CN.md": (
        f"在早于 `{FIRST_CHECKED_TAG}` 的标签上的运行会使用该标签自己的工作流，它缺少上文所述的部分检查，"
        "环境会拒绝它的部署。",
    ),
    ".github/workflows/pages.yml": (
        f"A run on a tag older than {FIRST_CHECKED_TAG} would take that tag's own workflow, which lacks some of the"
        " checks described above, and the environment refuses its deployment.",
    ),
}
# The same rules in the header comment of pages.yml, whose lines are joined with spaces, and the guard job's outputs.
HEADER_RULES = (
    "a published GitHub Release, not marked as a prerelease",
    "Every run, whichever of the two started it, first asks for the tag's Release",
    "one read-only GitHub API request",
    "a re-run keeps the event of the run it repeats",
    "A re-run that leaves out the guard job, such as one of the deploy job alone, deploys nothing, because the deploy"
    " job requires the guard job's record of the current attempt.",
    "Such a re-run fails in the re-run check job, with an error that says the attempt did not run the guard job and"
    " deployed nothing; re-run the whole run.",
    "It gives the commit of a run that passes all of this, and the run's attempt, as its outputs",
    "the commit the guard job approved in the same attempt",
    "three numbers without leading zeros",
    'A repository ruleset ("Release tags v*") lets only repository admins create, move or delete v* tags',
    f"the github-pages environment accepts deployments only from tags from {FIRST_CHECKED_TAG} on",
    "the two keep anyone but an admin from deploying through the github-pages environment with a workflow of their own",
)
# What the comments of the deploy job and of the re-run check job say about a re-run without the guard job: why the
# deploy job deploys nothing in it and that the re-run check job fails it; when the re-run check job runs, as GitHub
# runs it, and why its condition holds only in such a re-run; and what it may not do. Each comment is read at the job's
# own indent, its lines joined with spaces.
JOB_COMMENTS = {
    "deploy": (
        "The commit must be the one the guard job approved, in this attempt of the run: a re-run that leaves out the"
        " guard job, such as one of the deploy job alone, keeps the guard job's outputs of the attempt it repeats, so"
        " it deploys nothing, and the re-run check job below fails it.",
    ),
    RERUN_CHECK: (
        "A re-run that leaves out the guard job (the deploy job alone, or the failed jobs after the test, build or"
        " deploy job failed) skips the deploy job, and would end green with nothing deployed.",
        "GitHub re-runs a job together with every job that needs it, so such a re-run runs this job too, and it fails"
        " the run with the reason.",
        "!cancelled() has GitHub evaluate the condition whatever the jobs it needs did, unless the run was cancelled;"
        " it holds only if the guard job succeeded and its outputs name another attempt than this one, as they do when"
        " a re-run keeps them from the attempt it repeats.",
        "So this job is skipped in an attempt that ran the guard job, whether the deploy job deployed or not, in a run"
        " that the guard job refused, and in a cancelled run.",
        "It needs every job before it, as the deploy job does, runs no checkout and no action, and has no permissions.",
    ),
}
# What the READMEs and the comments of pages.yml said before the github-pages environment accepted deployments only
# from tags from v1.1.1 on (2026-10-12): an environment rule that allows every v* tag or main, a request to run only
# tags from v1.1.1 on by hand, and a manual run that deploys a release again without looking up its Release. Then,
# until it accepted them only from tags from v1.1.2 on (2026-10-13): that it accepted them from v1.1.1 on, that the
# workflow of an older tag does not look up the Release, and that a run on v1.1.1 takes a workflow whose deploy job does
# not yet require the guard job's record of the current attempt, so such a run must be re-run whole. And a clause of
# the header that could be read as the deploy job failing ("deploys nothing, because ..., and fails with the re-run
# check job's error"). Each phrase is kept out of its document read without case and with the line breaks of its
# comments joined (retired_phrases()).
RETIRED_DEPLOYMENT_RULES = {
    "README.md": (
        "allows `v*` tags",
        "must allow `v*` tags",
        "allows `main`",
        "should be run by hand",
        "redeploy",
        "deploys that release again",
        "deploys that tag's commit again",
        "from `v1.1.1` on",
        "which does not look up the Release",
        "A run on `v1.1.1` uses that tag's own workflow",
        "does not yet require",
        "re-run such a run whole",
    ),
    "README.zh-CN.md": (
        "允许 `v*` 标签",
        "允许 `main`",
        "只应手动运行",
        "重新部署",
        "`v1.1.1` 及之后",
        "它不会查询 Release",
        "在 `v1.1.1` 上的运行同样使用该标签自己的工作流",
        "尚不要求",
        "这样的运行应整次重新运行",
    ),
    ".github/workflows/pages.yml": (
        "allows v* tags",
        "allows main",
        "should be run by hand",
        "redeploy",
        "deploys it again",
        "deploys that commit again",
        "does not look up the tag's Release, so",
        "and fails with the re-run check job's error",
        "from v1.1.1 on",
        "which does not look up the Release",
        "A run on v1.1.1 takes v1.1.1's workflow",
        "does not yet require",
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


def retired_phrases(document, text):
    """The phrases of RETIRED_DEPLOYMENT_RULES[document] that text holds, read without case and with the line breaks of
    its comments joined (flowing()), so a phrase wrapped across two comment lines is found too."""
    read = flowing(text).casefold()
    return [phrase for phrase in RETIRED_DEPLOYMENT_RULES[document] if phrase.casefold() in read]


# Where a sentence of a deployment section or of the header comment ends: after a full stop and a space, but not after
# "e.g.", "i.e.", "etc." or "cf.", or after 。. A sentence that ends in "etc." is read with the next one, so a claim that
# runs on after it is found; a true pair can be taken for a claim (see the samples): put such a sentence another way.
SENTENCE_END = re.compile(r"(?<=\.)(?<![Ee]\.g\.)(?<![Ii]\.e\.)(?<!etc\.)(?<![Cc]f\.)\s+|(?<=。)")
# The same retired rules in other words. A sentence of a deployment section or of the header comment brings one back if
# it matches every pattern of that rule, read without case: it names the environment and main, unless all it says is
# that the environment refuses main (ENVIRONMENT_REFUSES_MAIN), or the environment and every (any, all, each) v* tag;
# it says a manual run is for tags from some vX.Y.Z on (onwards, or later, and newer, 及之后, 或之后, 以后, 或更新, 起;
# "on" only after "from", as "a manual run on v1.2.0 on the Actions page" is not; 或更新 not before README or 该, as
# 或更新 README, "or update README", and 或更新该标签的 Release are not); or it says a run deploys again (once more,
# 再次, 再一次, 又一次), unless its clause names a re-run of the whole run before that, which does deploy the tag's
# commit again.
ENVIRONMENT = re.compile(r"environment|环境", re.I)
MAIN = r"(?<![a-z])main(?![a-z])"
# A whole sentence that says the environment refuses main and nothing that could make it false: the environment or its
# rules as the subject, right before the refusal (rejects, refuses, does not accept, 拒绝, 不接受), so that no negation,
# hedge or "no longer" stands between them; (the) main (branch, 分支) right after the refusal; then at most the other tags
# it refuses, "and every tag older than `v1.1.2`" (和早于 `v1.1.2` 的所有标签), and a full stop. A word before the
# subject, between the subject and the refusal or between the refusal and main, and anything else after main leave the
# sentence to the rule, as does an adverb such as also, always or 也: such a sentence must be put another way.
FIRST_CHECKED = re.escape(FIRST_CHECKED_TAG)
ENVIRONMENT_REFUSES_MAIN = (
    r"\s*(?:the\s+|该\s*)?(?:`?github-pages`?\s*)?(?:environment|环境)(?:'s\s+rules|\s+rules|的(?:部署)?规则)?\s*"
    r"(?:rejects?|refuses?|does\s+not\s+accept|拒绝|不接受)\s*(?:the\s+)?`?main`?(?:\s*(?:branch|分支))?"
    rf"(?:\s+and\s+every\s+tag\s+older\s+than\s+`?{FIRST_CHECKED}`?|\s*和早于\s*`?{FIRST_CHECKED}`?\s*的所有标签)?[.。]?"
)
RETIRED_SENTENCES = {
    # A main in a sentence that says more than that the environment refuses main, or says it in other words.
    "the environment and main": (
        ENVIRONMENT,
        re.compile(rf"^(?!{ENVIRONMENT_REFUSES_MAIN}\Z)[\s\S]*?{MAIN}", re.I),
    ),
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
            rf"|{VERSION}`?\s*(?:onwards?\b|(?:or|and) (?:later|newer)\b"
            r"|[及或]?(?:之后|以后)|或更新(?!\s*`?README|该)|起)",
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


class DocumentationTests(unittest.TestCase):
    def section(self, readme):
        text = (ROOT / readme).read_text(encoding="utf-8")
        return text[text.index(DEPLOYMENT_SECTIONS[readme]) :].split("\n## ", 1)[0]

    def header(self):
        """The header comment of pages.yml, its lines joined with spaces."""
        lines = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8").splitlines()
        return " ".join(line.removeprefix("# ") for line in lines[: lines.index("on:")] if line.startswith("#"))

    def job_comment(self, job_id):
        """The comment lines of a job of pages.yml at the job's own indent, joined with spaces."""
        _, jobs = workflows.read_workflow("pages.yml")
        return " ".join(line.removeprefix("    # ") for line in jobs[job_id]["lines"] if line.startswith("    # "))

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
        self.assertEqual(workflows.yaml_scalar(jobs[RERUN_CHECK]["keys"]["name"][0], "name"), RERUN_CHECK_NAME)

    def test_the_job_comments_state_what_a_re_run_without_the_guard_job_does(self):
        for job_id, phrases in JOB_COMMENTS.items():
            comment = self.job_comment(job_id)
            for phrase in phrases:
                with self.subTest(job=job_id, phrase=phrase):
                    self.assertIn(phrase, comment)

    def test_a_manual_run_is_shown_only_on_a_tag_whose_workflow_checks_the_release(self):
        # A run takes pages.yml from the tag's commit, and the pages.yml of a tag older than FIRST_CHECKED_TAG lacks
        # some of the checks the documents describe, and the environment refuses its deployment: an example on such a
        # tag would not do what the text around it says.
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
        # The github-pages environment accepts deployments only from tags from FIRST_CHECKED_TAG on: not every v* tag,
        # and not main; that is a rule the environment enforces, not a request about manual runs, and every run looks up
        # the Release, so none deploys a release again without it.
        for document, phrases in RETIRED_DEPLOYMENT_RULES.items():
            text = (ROOT / document).read_text(encoding="utf-8")
            found = retired_phrases(document, text)
            for phrase in phrases:
                with self.subTest(document=document, stale=phrase):
                    self.assertNotIn(phrase, found)
            stale = NO_LOOKUP_ON_A_RE_RUN[DOCUMENT_LANGUAGES[document]]
            with self.subTest(document=document, stale=stale.pattern):
                self.assertEqual(stale.findall(flowing(text)), [])
            described = self.header() if document.endswith(".yml") else self.section(document)
            for sentence in SENTENCE_END.split(described):
                with self.subTest(document=document, sentence=sentence[:60]):
                    self.assertEqual(retired_rules(sentence), [])

    def test_a_retired_phrase_is_found_in_other_case_and_across_a_comment_line_break(self):
        # A comment of pages.yml may wrap at any space, at the left margin or indented in a job, and a phrase in other
        # case is the same phrase; another phrase is not one.
        workflow = ".github/workflows/pages.yml"
        for document, text, found in (
            (workflow, "# The environment allows\n# main.", ["allows main"]),
            (workflow, "    # The environment allows\n    # main for now.", ["allows main"]),
            (
                workflow,
                "    # A run on an older tag takes a workflow which\n    # does not look up the Release.",
                ["which does not look up the Release"],
            ),
            (workflow, "# THE ENVIRONMENT ALLOWS MAIN.", ["allows main"]),
            (workflow, "# The environment allows the main branch.", []),
            ("README.md", "The environment allows\n`main`.", ["allows `main`"]),
            ("README.zh-CN.md", "环境的部署规则只接受 `v1.1.1`\n及之后的标签。", ["`v1.1.1` 及之后"]),
        ):
            with self.subTest(text, document=document):
                self.assertEqual(retired_phrases(document, text), found)

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
            ("en", "    # A re-run of failed jobs does not look the\n    # Release up again.", True),
            ("en", OLDER_TAGS[".github/workflows/pages.yml"][0], False),
            ("en", "A run on an older tag takes that tag's own workflow, which does not look up the Release.", False),
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
            # A sentence that says only that the environment refuses main states its rule, in each form the pattern
            # knows.
            ("The environment rejects `main` and every tag older than `v1.1.2`.", []),
            ("The environment refuses main.", []),
            ("The `github-pages` environment rejects the `main` branch.", []),
            ("The github-pages environment does not accept main.", []),
            ("The environment's rules reject `main`.", []),
            ("The environment rules refuse the `main` branch.", []),
            ("The environment does not accept `main`.", []),
            ("Environment rules refuse the main branch and every tag older than v1.1.2.", []),
            ("该环境拒绝 `main`。", []),
            ("该环境不接受 `main` 分支。", []),
            ("该 `github-pages` 环境不接受 `main` 分支。", []),
            ("环境的部署规则拒绝 `main` 和早于 `v1.1.2` 的所有标签。", []),
            ("环境的规则拒绝main分支和早于v1.1.2的所有标签。", []),
            ("该环境拒绝 `main`。\n\n该环境不接受 `main`。", []),
            ("The environment refuses main", []),
            # These two are true as well, but an adverb stands between the subject and the refusal, so they are taken
            # for the claim (known false positives): put such a sentence another way.
            ("The environment also rejects `main`.", [main]),
            ("该环境也拒绝 `main`。", [main]),
            # Every other sentence that names main is taken for it: one that says the environment accepts main after
            # a refusal of something else or of main itself, or in a clause of its own; a refusal by another subject;
            # a refusal with a negation, a hedge or "no longer" in front of it, or one that a word before the subject
            # denies; a refusal of something other than main; and one that names other tags than those it refuses.
            ("The environment rejects old tags and accepts `main`.", [main]),
            ("The environment rejects `v1.1.1`; it accepts `main`.", [main]),
            ("环境拒绝旧标签，但接受 `main`。", [main]),
            ("The environment refuses main and also accepts main.", [main]),
            ("The environment rejects `main` and accepts it.", [main]),
            ("The environment rejects `main`, but accepts `main` for manual runs.", [main]),
            ("The environment refuses main.It accepts main.", [main]),
            ("该环境拒绝 `main` 以外的分支。", [main]),
            ("The guard job refuses `main`, but the environment accepts it.", [main]),
            ("Release guard 任务拒绝 `main`，但环境接受它。", [main]),
            ("The environment does not reject `main`.", [main]),
            ("The environment never rejects `main`.", [main]),
            ("The environment doesn't refuse `main`.", [main]),
            ("The environment no longer rejects `main`.", [main]),
            ("The environment doesn't ever reject `main`.", [main]),
            ("The environment does not always reject `main`.", [main]),
            ("该环境不拒绝 `main`。", [main]),
            ("该环境并未拒绝 `main`。", [main]),
            ("该环境没有拒绝 `main`。", [main]),
            ("该环境不会拒绝 `main`。", [main]),
            ("该环境不再拒绝 `main`。", [main]),
            ("该环境没拒绝 `main`。", [main]),
            ("该环境不能拒绝 `main`。", [main]),
            ("该环境无法拒绝 `main`。", [main]),
            ("该环境不曾拒绝 `main`。", [main]),
            ("该环境没再拒绝 `main`。", [main]),
            ("该环境已不再拒绝 `main`。", [main]),
            ("It is not true that the environment refuses main.", [main]),
            ("The environment rejects non-main.", [main]),
            ("该环境拒绝非 `main` 分支。", [main]),
            ("The environment rejects `main` and every tag older than `v1.1.3`.", [main]),
            ("该环境拒绝 `main` 和早于 `v1.1.3` 的所有标签。", [main]),
            # And the claim itself, and sentences that differ from the rule's form in one word or mark, which a looser
            # pattern would let through: one that says the environment accepts (allows, does accept, does also accept,
            # 接受, 允许) main; a refusal in the past tense, or one that a word before the subject (or before 该),
            # another determiner than the (该) or old rules date or deny; main inside a longer word (非main, main以外);
            # a tail with another word in place of and (和), every (所有) or older (早于); and a question.
            ("The environment accepts `main`.", [main]),
            ("The environment allows `main`.", [main]),
            ("该环境接受 `main`。", [main]),
            ("该环境允许 `main`。", [main]),
            ("The environment does accept `main`.", [main]),
            ("The environment does also accept `main`.", [main]),
            ("The environment rejected `main`.", [main]),
            ("Formerly the environment rejects `main`.", [main]),
            ("以前该环境拒绝 `main`。", [main]),
            ("No environment rejects `main`.", [main]),
            ("没有环境拒绝 `main`。", [main]),
            ("The environment's old rules reject `main`.", [main]),
            ("该环境的旧规则拒绝 `main`。", [main]),
            ("该环境拒绝非main。", [main]),
            ("该环境拒绝main以外的分支。", [main]),
            ("The environment rejects `main` unlike every tag older than `v1.1.2`.", [main]),
            ("The environment rejects `main` and no tag older than `v1.1.2`.", [main]),
            ("The environment rejects `main` and every tag newer than `v1.1.2`.", [main]),
            ("该环境拒绝 `main` 非早于 `v1.1.2` 的所有标签。", [main]),
            ("该环境拒绝 `main` 和晚于 `v1.1.2` 的所有标签。", [main]),
            ("该环境拒绝 `main` 和早于 `v1.1.2` 的部分标签。", [main]),
            ("The environment rejects `main`?", [main]),
            ("该环境拒绝 `main`？", [main]),
            # No sentence ends at an abbreviation.
            ("The `github-pages` environment, e.g. for `main`, accepts it.", [main]),
            ("The environment covers one branch, E.g. `main`.", [main]),
            ("The environment, i.e. its rules, admits `main`.", [main]),
            ("The environment covers one branch, I.e. `main`.", [main]),
            ("The environment accepts tags etc. and also `main`.", [main]),
            # A sentence that ends in "etc." is read with the next one, so "etc. Also `main`." brings the claim back;
            # so does the true pair after it, a known false positive: put such a pair another way.
            ("The environment accepts `v1.2.0`, `v1.3.0` etc. Also `main`.", [main]),
            ("The environment accepts `v1.2.0`, `v1.3.0` etc. Main is a branch, not a tag.", [main]),
            ("The environment (cf. `main`) accepts it.", [main]),
            ("The environment accepts it (Cf. `main`).", [main]),
            ("The `github-pages` environment allows every `v*` tag.", [every]),
            ("The github-pages environment allows any v* tag.", [every]),
            ("The environment accepts all `v*` tags.", [every]),
            ("The environment accepts each `v*` tag.", [every]),
            ("Every `v*` tag is accepted by the environment.", [every]),
            ("The environment accepts every tag `v*`.", [every]),
            # every, any, all and each count as whole words only: "many" is not "any".
            ("The environment accepts many `v*` tags.", []),
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
            ("手动运行仅限 `v1.1.1` 或更新。", [manual]),
            ("只应对 `v1.1.2` 或更新版本的标签手动运行。", [manual]),
            ("手动运行仅限 `v1.1.2` 或更新版本。", [manual]),
            ("只应对 `v1.1.2` 或更新标签手动运行。", [manual]),
            ("只应对 `v1.1.2` 或更新 的标签手动运行。", [manual]),
            ("手动运行仅限 `v1.1.2` 或更新以上版本。", [manual]),
            ("只应对 `v1.1.2` 或更新 手动运行。", [manual]),
            ("手动运行仅限 `v1.1.2` 或更新者。", [manual]),
            # 或更新 before README or 该 is "or update" it.
            ("手动运行 `v1.1.3` 或更新 README 中的版本号。", []),
            ("手动运行 `v1.1.3` 或更新该标签的 Release。", []),
            ("手动运行 `v1.1.3` 或更新`README.md`。", []),
            # 或更新 before another object, as in these two, is "or update" it as well, but is taken for "or newer"
            # (known false positives): put such a sentence another way.
            ("手动运行 `v1.1.3` 或更新版本号。", [manual]),
            ("手动运行 `v1.1.3` 或更新文档。", [manual]),
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
            ("A run deploys only a release, and to try again you re-run the whole run.", []),
            ("A run deploys only a release: to try again, re-run the whole run.", []),
            # A re-run of the whole run does deploy again, but only a clause that names it first is about it.
            ("A re-run of the whole run deploys the tag's commit again.", []),
            ("重新运行整次运行会再次部署该标签的提交。", []),
            # Only a re-run of the whole run: another re-run that is said to deploy again is the false claim itself.
            ("A re-run of the deploy job alone deploys the tag's commit again.", [again]),
            ("A re-run of the failed jobs deploys the release again.", [again]),
            ("只重新运行部署任务会再次部署该标签的提交。", [again]),
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


if __name__ == "__main__":
    unittest.main()
