"""PR-delivery tests — a verified change comes back as a reviewable branch/PR.

The push + GitHub PR path needs a real remote + auth (live-validated, like the
sandbox runs); these cover the local, deterministic part: repo detection, the
no-changes / non-repo guards, branch+commit, graceful no-remote degradation, and
the TaskQueue wiring (a done open_pr task triggers delivery; a plain task doesn't).
"""

import os
import subprocess

import pytest

from devclaw import delivery
from devclaw.delivery import (
    _NO_AGENT_COMMIT_LEAD,
    _closes_issues,
    _extract_pr_url,
    _goal_pr_body,
    _is_advance_brief,
    _pr_body,
    _pr_title,
    _resolve_title,
    _scope_label,
    _slug,
    deliver_change,
)
from devclaw.task_change import MACHINE_COMMIT_SUBJECT

# The thin-advance pull-brief shape (goal/tick.py:_advance_brief) — the generic
# task ``goal`` on every long_lived tick post-demolition.
_ADVANCE_BRIEF = (
    "Advance this goal by one substantive, shippable increment, then stop.\n"
    "Implement the CURRENT milestone only — the smallest not-yet-done "
    "milestone in PLAN.md — and do NOT build ahead into later milestones; a "
    "coherent one-milestone slice is one reviewable PR.\n"
    "First read PLAN.md (create and maintain it as you go) and the repo to "
    "decide the next step yourself; implement it end to end and commit, "
    "PLAN.md included.\n\nGoal: Build a small field notes REST API"
)
from devclaw.engine import EngineRequest
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _init_repo(path) -> None:
    _git(path, "init", "-q")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init")


def _branch(path) -> str:
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


# ---- pure helpers ----------------------------------------------------------


def test_slug():
    assert _slug("Add a GET /api/version endpoint!") == "add-a-get-api-version-endpoint"
    assert _slug("") == "change"
    # truncates on a word boundary, never mid-word
    long = _slug("Add a GET api crons id endpoint that returns the single cron")
    assert len(long) <= 40 and not long.endswith("-") and "-ret" not in long


def test_pr_title_is_clean_and_conventional():
    # conventional-commit prefix from kind; backticks stripped; word-boundary cut
    t = _pr_title("Add a `GET /api/crons/{id}` endpoint", kind="implement_feature")
    assert t.startswith("feat: ")
    assert "`" not in t
    assert _pr_title("Harden the reject path", kind="fix_bug").startswith("fix: ")
    # long goals are truncated on a word boundary with an ellipsis, within the cap
    longt = _pr_title("Add " + "word " * 40, kind="implement_feature")
    assert len(longt) <= 72 and longt.endswith("…")
    # no kind → no prefix, still cleaned
    assert _pr_title("just do the thing").startswith("just do the thing")


def test_resolve_title_prefers_worker_subject_over_the_advance_brief():
    # Regression (the two-commit `devclaw/…advance-this-goal` PR): the worker
    # committed a clean `feat(tags): …`, the task goal is the generic advance
    # brief. The worker's subject must win — title, branch, and body all describe
    # WHAT CHANGED, never the plumbing brief.
    title, branch, changes = _resolve_title(
        planner_title=None,
        agent_msg=("feat(tags): add Tags with note assignment", "Adds Tag entity + M2M."),
        goal=_ADVANCE_BRIEF, kind="implement_feature", task_id="02c2b647aa",
    )
    assert title == "feat(tags): add Tags with note assignment"
    assert branch == "feat/add-tags-with-note-assignment"
    assert "Advance this goal" not in title and "devclaw/" not in branch
    assert changes == "Adds Tag entity + M2M."


def test_resolve_title_decorates_with_resolved_issue():
    # A self-fix goal names its target issue; the title gets a trailing `(#N)`
    # and the branch is prefixed with the number — the linked-ticket parallel.
    title, branch, _ = _resolve_title(
        planner_title=None,
        agent_msg=("fix(feed): stop pagination drift", "Cursor paging. Fixes #42."),
        goal="Fix devclaw issue #42: pagination drifts",
        kind="fix_bug", task_id="abc123def4",
    )
    assert title == "fix(feed): stop pagination drift (#42)"
    assert branch == "fix/42-stop-pagination-drift"


def test_resolve_title_no_worker_commit_returns_machine_commit_subject():
    # Spec 017 criterion 2: when there is no worker commit and no planner title,
    # the PR title must be the machine-commit subject — NEVER any text from the
    # dispatch prompt. This means the advance brief can't leak (the original
    # intent of the old test) AND no other prompt text can leak either.
    title, branch, changes = _resolve_title(
        planner_title=None, agent_msg=None,
        goal=_ADVANCE_BRIEF, kind="implement_feature", task_id="deadbeef12",
    )
    assert title == MACHINE_COMMIT_SUBJECT
    assert "Advance this goal by one substantive" not in title
    assert "Build a small field notes REST API" not in title
    assert "snapshot" in branch   # branch is `devclaw/<task_id[:8]>-snapshot`
    assert changes is None
    assert _is_advance_brief(_ADVANCE_BRIEF) and not _is_advance_brief("add a widget")
    # Same for a plain (non-brief) goal: dispatch prompt still cannot source title.
    title2, _, _ = _resolve_title(
        planner_title=None, agent_msg=None,
        goal="add a widget", kind="implement_feature", task_id="deadbeef12",
    )
    assert title2 == MACHINE_COMMIT_SUBJECT


def test_goal_branch_pr_body_never_renders_the_advance_brief():
    # The #538 shakedown live find: the GOAL-BRANCH path fed the raw advance
    # brief into the PR body (and title), so a delivered PR read as its own
    # dispatch instructions ("Advance this goal by one substantive…"). Same
    # rule as _resolve_title: the brief is plumbing — render the embedded
    # objective instead. Presence AND absence.
    body = _goal_pr_body(
        _ADVANCE_BRIEF, "deadbeef12", None,
        ["feat: add truncate_words utility"],
    )
    assert "Advance this goal by one substantive" not in body
    assert body.startswith("Build a small field notes REST API")
    assert "feat: add truncate_words utility" in body  # increments list kept
    # A non-brief goal (one-shot on a goal branch) still renders verbatim.
    plain = _goal_pr_body("add a widget", "deadbeef12", None, [])
    assert plain.startswith("add a widget")


def test_goal_branch_pr_title_fallback_strips_issue_pointer_preamble_and_truncates_at_word_boundary():
    # The S1 run-001 live find (#551): an intake-filed objective starts with the
    # pointer preamble `Implement intake issue #N of <owner>/<repo>:` — a ticket
    # reference, not a description. Left in, it ate the 72-char budget and the
    # title truncated to `…-s1-001: a…`. The pointer must be stripped from any
    # objective-derived title (the ref still lands in the body as `Closes #N`),
    # and the remainder truncates at a word boundary, never mid-word.
    goal = (
        "Implement intake issue #2 of dsdevq/devclaw-shakedown-s1-001: "
        "a minimal field notes REST API with create, list, and delete endpoints "
        "plus persistence"
    )
    t = _pr_title(goal, kind="implement_feature")
    assert t == "feat: a minimal field notes REST API with create, list, and delete…"
    assert "intake issue" not in t and "dsdevq/" not in t and "#2" not in t
    assert len(t) <= 72
    # word-boundary policy: the fragment before the ellipsis is a whole word
    assert t[len("feat: "):].rstrip("…").split(" ")[-1] in goal.split()
    # a short pointer objective renders whole, pointer gone
    assert (
        _pr_title(
            "Implement intake issue #2 of dsdevq/devclaw-shakedown-s1-001: a minimal notes API",
            kind="implement_feature",
        )
        == "feat: a minimal notes API"
    )
    # the stripped ref still reaches the PR body as Closes #N
    assert _closes_issues(goal) == [2]
    assert "Closes #2" in _goal_pr_body(goal, "deadbeef12", None, [])
    # a pointer with nothing after it degrades to the unstripped summary, not ""
    assert _pr_title("Implement intake issue #3 of o/r: ").strip()


def test_scope_label_from_cc_scope_then_type():
    assert _scope_label("feat(tags): add tag filtering") == "tags"
    assert _scope_label("fix: harden the reject path") == "fix"
    assert _scope_label("not a conventional subject") == ""


def test_pr_body_uses_summary_and_testing_sections():
    # Claude-Code house structure (matches the reference the owner likes): a
    # `## Summary` of what changed and a `## Testing` note, with a clean
    # generated-by signature — no task-UUID "Delivered by devclaw" footer.
    verify = {"ran": True, "cmd": "dotnet test", "passed": True, "exit_code": 0}
    # With a worker commit body (changes=), the summary shows that text.
    body = _pr_body("Add an endpoint", "abcd1234", verify,
                    changes="feat: add endpoint\n\nDid the thing.")
    assert "## Summary" in body and "Did the thing." in body
    assert "Add an endpoint" not in body   # goal text NOT in body (criterion 2)
    assert "## Testing" in body and "Verified with `dotnet test` — passing." in body
    assert "🤖 Generated with [Claude Code]" in body
    assert "Delivered by devclaw" not in body
    # Without a worker commit (changes=None): body explicitly says so, never
    # echoes the dispatch prompt (spec 017 criterion 3).
    nocommit = _pr_body("x", "id", verify)
    assert "## Summary" in nocommit
    assert "Agent authored no commit" in nocommit
    assert "x" not in nocommit.split("## Summary")[1].split("## Testing")[0].strip()
    # degrades cleanly when there was no gate → no ## Testing section at all
    nogate = _pr_body("x", "id", None)
    assert "Verified with" not in nogate and "## Testing" not in nogate


def test_pr_body_omits_original_task_dump_and_coauthor_trailer():
    # The verbose <details>Original task</details> block is gone, and the worker's
    # Co-Authored-By trailer stays in the COMMIT — never duplicated into the PR body.
    body = _pr_body(
        "the long ticket instruction here",
        "id",
        {"ran": True, "cmd": "pytest", "passed": True},
        changes="feat: do the thing\n\n- did X\n- did Y\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
    )
    assert "Original task" not in body and "<details>" not in body
    assert "Co-Authored-By" not in body
    assert "the long ticket instruction here" not in body   # task text not dumped
    assert "did X" in body and "## Summary" in body


def test_pr_body_carries_no_line_count_telemetry():
    # The whole point of this change: a PR reads like a human wrote it, with no
    # diffstat / files-changed / line-count telemetry anywhere in the body.
    verify = {"ran": True, "cmd": "pytest", "passed": True, "exit_code": 0}
    body = _pr_body(
        "Refactor the feed", "id", verify,
        changes="feat(feed): paginate\n\nCursor paging.",
    )
    for banned in ("Files changed", "insertion", "deletion", "spans ", "files changed", "```"):
        assert banned not in body, f"telemetry leaked: {banned!r}"


def test_pr_body_drops_the_green_gate_caveat():
    # The "green gate means tests pass, not that the code is right" disclaimer is
    # gone — the safety rail is the fail-closed gate + human merge, not PR prose.
    body = _pr_body("Add X", "id", {"ran": True, "cmd": "pytest", "passed": True})
    assert "not that the code" not in body
    assert "green gate" not in body.lower()


def test_pr_body_links_resolved_issue_with_closes():
    # A self-fix goal names its target issue; a `Closes #N` line makes GitHub
    # link and auto-close it on merge (the linked-ticket parallel).
    body = _pr_body("Fix devclaw issue #42: pagination drifts", "id", None)
    assert "Closes #42" in body
    # Also picks the engineer's own `Fixes #N` out of the commit body — and
    # canonicalizes it to a single `Closes #7` (no redundant directive in prose).
    body2 = _pr_body("Advance the goal", "id", None, changes="feat: x\n\nFixes #7")
    assert "Closes #7" in body2 and "Fixes #7" not in body2
    # A passing mention never triggers a spurious auto-close.
    assert "Closes" not in _pr_body("See #99 for background", "id", None)


def test_pr_body_renders_trust_advisory_section():
    # ADR 0007: a trust-mode gate advisory rides into the PR body so the human
    # sees it at the merge boundary. Absent when there are no advisories.
    advisories = [{"gate": "review", "reason": "dead code left in DealService"}]
    body = _pr_body("Add X", "id", None, advisories=advisories)
    assert "Advisory" in body and "shipped under `trust`" in body
    assert "review" in body and "dead code left in DealService" in body
    # no advisory section when the list is empty/None
    assert "Advisory" not in _pr_body("Add X", "id", None)


def test_pr_body_never_echoes_dispatch_prompt_when_no_agent_commit():
    """Spec 017 criterion 3: when changes=None (agent committed nothing), the PR
    body must say so EXPLICITLY rather than silently presenting the goal text as
    a description of the change."""
    body = _pr_body("Add a very specific endpoint", "id", None)
    assert "Agent authored no commit" in body            # explicit, not silent
    assert "Add a very specific endpoint" not in body   # prompt text never leaks
    # The constant is in the summary section.
    assert _NO_AGENT_COMMIT_LEAD in body


def test_closes_issues_extraction():
    # Explicit fix/close verbs and the self-fix `issue #N` objective count…
    assert _closes_issues("Fix devclaw issue #42: x") == [42]
    assert _closes_issues("feat: y\n\nFixes #7 and closes #8") == [7, 8]
    assert _closes_issues("resolved #3") == [3]
    # …a passing mention does not (even one using the word "issue"), and
    # results dedupe in first-seen order.
    assert _closes_issues("see #99 for context") == []
    assert _closes_issues("see issue #50 for context, do not close it") == []
    assert _closes_issues("Fixes #5", "Fix devclaw issue #5 again") == [5]


def test_extract_pr_url():
    out = "https://github.com/lifekit-hq/lifekit-dashboard/pull/12\n"
    assert _extract_pr_url(out) == "https://github.com/lifekit-hq/lifekit-dashboard/pull/12"
    assert _extract_pr_url("nothing here") is None


# ---- deliver_change (real local git, no remote) ----------------------------


async def test_deliver_commits_to_a_branch_and_degrades_without_remote(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _init_repo(repo)
    (tmp_path / "repo" / "new.txt").write_text("the agent's change\n")  # dirty tree

    r = await deliver_change(workspace_dir=repo, task_id="abcd1234ef", goal="add new file")

    assert r["committed"] is True
    # Branch is machine-capture slug (not derived from goal) — spec 017 criterion 2.
    assert r["branch"] == "devclaw/abcd1234-snapshot"
    assert r["pushed"] is False and r["pr_url"] is None
    assert r["delivered"] is True
    assert "no 'origin' remote" in r["error"]
    assert _branch(repo) == "devclaw/abcd1234-snapshot"  # change is on the branch


async def test_deliver_rejects_non_git_dir(tmp_path):
    r = await deliver_change(workspace_dir=str(tmp_path), task_id="x", goal="g")
    assert r["committed"] is False and "not a git repository" in r["error"]


async def test_deliver_noop_when_clean(tmp_path):
    repo = str(tmp_path / "clean")
    os.makedirs(repo)
    _init_repo(repo)
    r = await deliver_change(workspace_dir=repo, task_id="x", goal="g")
    assert r["committed"] is False and "no changes to deliver" in r["error"]


async def test_deliver_uses_the_engineer_commit_for_branch_not_the_goal(tmp_path):
    # The agent committed its own change with a conventional-commit message. The
    # delivered branch (and title/body) must describe WHAT CHANGED — derived from
    # the agent's commit — NOT the raw task instruction.
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "clone", "-q", origin, repo], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    # establish a base on the remote default branch
    (tmp_path / "repo" / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "HEAD")
    # simulate the agent: a commit with a real conventional message, clean tree
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    # the GOAL is a long instruction; it must NOT shape the branch.
    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef",
        goal="Read PRD.md first, then implement the widget API in one coherent change...",
        kind="implement_feature",
    )

    assert r["committed"] is True and r["pushed"] is True and r["delivered"] is True
    # branch is derived from the engineer's commit subject, not the goal slug
    assert r["branch"] == "feat/add-the-widget-endpoint"
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", origin], capture_output=True, text=True
    ).stdout
    assert "feat/add-the-widget-endpoint" in refs and "devclaw/" not in refs


async def test_deliver_uses_worker_subject_even_with_a_stray_dirty_file(tmp_path):
    # The exact compounding-test PR #2 bug: the worker committed its feature with
    # a clean `feat(tags): …` subject but left ONE stray file uncommitted, and the
    # task goal is the generic thin-advance brief. The old `not dirty and ahead>0`
    # guard discarded the worker's subject and dumped the brief onto a
    # `devclaw/…advance-this-goal` branch. Now the worker's subject wins; the stray
    # remainder is still committed onto the same branch.
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "clone", "-q", origin, repo], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (tmp_path / "repo" / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "HEAD")
    # the agent committed its feature cleanly…
    (tmp_path / "repo" / "tags.cs").write_text("// tags feature\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(tags): add Tags feature")
    # …but left a stray uncommitted file (e.g. PLAN.md it forgot to stage)
    (tmp_path / "repo" / "PLAN.md").write_text("- [x] Feature 2: Tags\n")

    r = await deliver_change(
        workspace_dir=repo, task_id="02c2b647aa",
        goal=_ADVANCE_BRIEF, kind="implement_feature",
    )

    assert r["committed"] is True and r["pushed"] is True and r["delivered"] is True
    assert r["branch"] == "feat/add-tags-feature"
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", origin], capture_output=True, text=True
    ).stdout
    assert "feat/add-tags-feature" in refs
    assert "advance-this-goal" not in refs and "devclaw/" not in refs


async def test_deliver_goal_branch_mode_does_not_create_per_task_branch(tmp_path):
    """Pillar 2: when the workspace is on a ``goal/<id>`` branch (because
    prepare_workspace put it there), deliver_change keeps the change ON that
    branch — no per-task devclaw/* branch. The goal branch becomes the
    durable thing every item commits to so the cumulative work stacks into
    ONE PR. Closes the 2026-06-26 PR-fan-out failure where 11 separate PRs
    each rebuilt the foundation in conflicting paths."""
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "clone", "-q", origin, repo], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (tmp_path / "repo" / "base.txt").write_text("base\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "HEAD")
    # Simulate prepare_workspace having checked out the goal branch.
    _git(repo, "checkout", "-b", "goal/my-goal")
    # Agent makes its commit on the goal branch.
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef",
        goal="implement the widget", kind="implement_feature",
    )

    assert r["committed"] is True and r["pushed"] is True and r["delivered"] is True
    # CRITICAL: branch stays on the goal branch — no devclaw/* fork.
    assert r["branch"] == "goal/my-goal"
    assert _branch(repo) == "goal/my-goal"
    # The push landed the change on origin/goal/my-goal (the durable target).
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", origin], capture_output=True, text=True,
    ).stdout
    assert "goal/my-goal" in refs and "devclaw/" not in refs and "feat/" not in refs


async def test_deliver_uses_explicit_planner_title_over_engineer_commit(tmp_path):
    """C7 climb: when the planner emits a `title:` on the Action, it wins over
    the engineer's own commit subject and the goal-derived heuristic. Closes
    the failure mode where a mid-work commit subject describes only part of
    what was asked (planner has full intent; commit describes latest step)."""
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", origin], check=True)
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "clone", "-q", origin, repo], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (tmp_path / "repo" / "base.txt").write_text("base\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "HEAD")
    # engineer's commit describes only their last atomic step.
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: iterate on the parser")

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef",
        goal="Rewrite the parser to support nested groups...",
        kind="implement_feature",
        title="feat(parser): support nested groups",
    )

    assert r["committed"] is True and r["pushed"] is True and r["delivered"] is True
    # branch is derived from the planner's title, not from `wip: iterate…`.
    assert r["branch"] == "feat/support-nested-groups"


async def test_deliver_explicit_title_prefixes_kind_when_bare(tmp_path):
    """A planner-supplied title without a conventional-commit prefix still gets
    the kind-derived prefix, so the delivered PR reads as feat: / fix: even
    when the planner didn't remember the shape."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    _init_repo(repo)
    (tmp_path / "repo" / "new.txt").write_text("change\n")

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef",
        goal="do the thing", kind="implement_feature",
        title="add /health endpoint",
    )

    # local-only (no remote) — but the branch was derived from the title.
    assert r["committed"] is True
    assert r["branch"] == "feat/add-health-endpoint"


async def test_current_branch_helper_returns_branch_or_none(tmp_path):
    from devclaw.delivery import _current_branch

    # Non-repo → None (graceful, not crash).
    assert await _current_branch(str(tmp_path)) is None

    # Real repo with a checked-out branch → returns its name.
    repo = str(tmp_path / "r")
    os.makedirs(repo)
    _init_repo(repo)
    assert (await _current_branch(repo)) in ("main", "master")


def test_cc_helpers_and_changes_body():
    from devclaw.delivery import _cc_type, _cc_description, _looks_conventional
    assert _looks_conventional("feat(api): add x") and not _looks_conventional("add x")
    assert _cc_type("fix(db): y", "implement_feature") == "fix"      # from the subject
    assert _cc_type("just a subject", "fix_bug") == "fix"            # falls back to kind
    assert _cc_description("feat(api): add the widget") == "add the widget"
    # the changes-path body leads with what changed under ## Summary; the original
    # task is NOT dumped into the body.
    body = _pr_body("the long ticket instruction", "id", None, changes="Added a widget endpoint + tests")
    assert "## Summary" in body and "Added a widget endpoint + tests" in body
    assert "Original task" not in body and "the long ticket instruction" not in body


# ---- TaskQueue wiring ------------------------------------------------------


def _writing_runner(filename: str):
    async def runner(req: EngineRequest):
        with open(os.path.join(req.workspace_dir, filename), "w") as f:
            f.write("change\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": {"ran": True, "cmd": "x", "passed": True,
                           "exit_code": 0, "timed_out": False, "output": ""}}
    return runner


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


async def test_open_pr_task_triggers_delivery(store, tmp_path):
    repo = str(tmp_path / "ws")
    os.makedirs(repo)
    _init_repo(repo)
    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="add feature", deliver=True)
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done"
    assert _branch(repo).startswith("devclaw/")        # delivery branched the change
    assert t.pr_url is None                              # no remote → local branch, recorded as None


async def test_done_is_not_observable_before_delivery(store, tmp_path, monkeypatch):
    """The pr_url close-out invariant: a deliver task must never be observable as
    'done' before its PR is recorded — else a poller (goalclaw) reads
    done-without-PR and re-dispatches. So delivery runs while the task is still
    'running', and 'done' + pr_url land in the same write."""
    repo = str(tmp_path / "ws3")
    os.makedirs(repo)
    _init_repo(repo)

    seen = {}
    pr = "https://github.com/lifekit-hq/lifekit-dashboard/pull/99"

    async def fake_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None,
                     title=None, advisories=None, **kw):
        # While delivery runs, the task must still be 'running' (not yet 'done').
        seen["status_during_delivery"] = store.get_task(task_id).status
        seen["pr_url_during_delivery"] = store.get_task(task_id).pr_url
        return {"delivered": True, "pr_url": pr, "branch": "devclaw/x", "pushed": True}

    monkeypatch.setattr("devclaw.queue.settle.deliver_change", fake_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="add feature", deliver=True)
    await q.drain()

    assert seen["status_during_delivery"] == "running"   # not 'done' yet
    assert seen["pr_url_during_delivery"] is None         # PR not recorded yet
    t = store.get_task(tid)
    assert t.status == "done" and t.pr_url == pr          # both land together


async def test_plain_task_does_not_deliver(store, tmp_path):
    repo = str(tmp_path / "ws2")
    os.makedirs(repo)
    _init_repo(repo)
    start_branch = _branch(repo)
    q = TaskQueue(store, runner=_writing_runner("x.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="g")  # deliver defaults False
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert _branch(repo) == start_branch                 # no delivery branch created


# ---- broken delivery must not settle done (T0.1) ----------------------------


def test_delivery_failed_classifies_benign_vs_broken():
    from devclaw.delivery import delivery_failed

    # benign: nothing to ship / local-only repo → not a failure
    assert delivery_failed({"error": None, "pr_url": "x"}) is None
    assert delivery_failed({"error": "no changes to deliver"}) is None
    assert delivery_failed({"error": "no 'origin' remote — left change on a local branch"}) is None
    # broken: the attempt itself failed at a step it tried
    assert delivery_failed({"error": "workspace is not a git repository"})
    assert delivery_failed({"error": "branch failed: fatal: ..."})
    assert delivery_failed({"error": "commit failed: ..."})
    assert delivery_failed({"error": "push failed (check repo push auth): remote rejected"})
    assert delivery_failed({"error": "pushed, but gh pr create failed: auth"})


async def test_broken_delivery_settles_failed_not_done(store, tmp_path, monkeypatch):
    """The false-green closure: a verified change whose push/PR BROKE must
    settle 'failed' with the delivery error — never 'done' with pr_url=None,
    which every poller upstream (the goal layer) reads as shipped."""
    repo = str(tmp_path / "ws4")
    os.makedirs(repo)
    _init_repo(repo)

    async def broken_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None,
                     title=None, advisories=None, **kw):
        return {"delivered": True, "branch": "devclaw/x", "committed": True,
                "pushed": False, "pr_url": None,
                "error": "push failed (check repo push auth): remote rejected"}

    monkeypatch.setattr("devclaw.queue.settle.deliver_change", broken_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="add feature", deliver=True)
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "failed"
    assert "delivery failed" in (t.error or "") and "push failed" in (t.error or "")
    assert t.pr_url is None


async def test_delivery_exception_settles_failed_not_done(store, tmp_path, monkeypatch):
    """deliver_change promises never to raise; if it does anyway, the task must
    fail loudly — not settle 'done' with the error swallowed to stderr."""
    repo = str(tmp_path / "ws5")
    os.makedirs(repo)
    _init_repo(repo)

    async def raising_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None,
                     title=None, advisories=None, **kw):
        raise RuntimeError("gh exploded")

    monkeypatch.setattr("devclaw.queue.settle.deliver_change", raising_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="add feature", deliver=True)
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "failed"
    assert "gh exploded" in (t.error or "")


async def test_no_changes_delivery_still_settles_done(store, tmp_path):
    """Benign no-PR outcome: the gate passed but the agent changed nothing
    (e.g. the requirement already held). Nothing was shipped because nothing
    existed to ship — that is a 'done' without a PR, not a failure. Since spec
    013 the emptiness is decided by the MATERIALIZED span before delivery is
    ever called, so the task carries an explicit ``no_change`` flag instead of a
    delivery verdict. (The no-remote sibling case is covered by
    test_open_pr_task_triggers_delivery.)"""
    repo = str(tmp_path / "ws6")
    os.makedirs(repo)
    _init_repo(repo)

    async def clean_runner(req: EngineRequest):
        # writes nothing — clean tree at delivery time
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": {"ran": True, "cmd": "x", "passed": True,
                           "exit_code": 0, "timed_out": False, "output": ""}}

    q = TaskQueue(store, runner=clean_runner)
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="g", deliver=True)
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "done" and t.pr_url is None
    # the explicit no-change outcome rides along in the persisted result
    import json as _json
    persisted = _json.loads(t.result_json)
    assert persisted["no_change"] is True
    assert persisted["change"]["status"] == "no_change"
    assert "delivery" not in persisted  # delivery was never attempted


# ---- branch-target delivery seam (v1-helper-resurface P1) -------------------


def _clone_with_origin(tmp_path):
    """The bare-origin + clone fixture the engineer-commit tests use, factored:
    a local bare origin, a configured clone, one 'base' commit pushed."""
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", origin], check=True)
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "clone", "-q", origin, repo], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (tmp_path / "repo" / "base.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "push", "-q", "origin", "HEAD")
    return origin, repo


def _github_faking_run(monkeypatch, calls, *, existing_pr=None, push_fails=False,
                       create_fails=False, label_fails=False):
    """Wrap delivery._run: git stays REAL (real branches, real pushes to the
    local bare origin), but the remote LOOKS like GitHub and ``gh`` is faked —
    the existing tests never reach the gh path, and this is the only seam that
    lets a test observe the ``gh pr create`` argv."""
    real_run = getattr(delivery._run, "_devclaw_real", delivery._run)

    async def fake_run(prog, *args, cwd, **kw):
        calls.append((prog, *args))
        if prog == "git" and args[:3] == ("remote", "get-url", "origin"):
            return 0, "https://github.com/acme/widgets.git"
        if push_fails and prog == "git" and args and args[0] == "push":
            return 1, "remote rejected"
        if prog == "gh" and args[:2] == ("pr", "list"):
            return 0, existing_pr or ""
        if prog == "gh" and args[:2] == ("pr", "create"):
            if create_fails:
                return 1, "gh: base branch not found"
            return 0, "https://github.com/acme/widgets/pull/7"
        if prog == "gh" and args[:2] in (("label", "create"), ("pr", "edit")):
            return (1, "gh: labels are broken today") if label_fails else (0, "")
        return await real_run(prog, *args, cwd=cwd, **kw)

    fake_run._devclaw_real = real_run  # so re-patching in one test never chains
    monkeypatch.setattr(delivery, "_run", fake_run)


async def test_deliver_target_branch_stays_on_pinned_branch_and_reuses_its_pr(tmp_path, monkeypatch):
    """v1-helper-resurface P1 (O4): a caller-pinned ``target_branch`` the
    workspace is already on rides the SAME reuse machinery as goal branches —
    delivery stays on the pinned branch (no fresh feat/* fork from the
    engineer's commit subject) and reuses its existing open PR instead of
    gh-pr-create'ing over it."""
    origin, repo = _clone_with_origin(tmp_path)
    # Simulate prepare_workspace having checked out the caller's branch.
    _git(repo, "checkout", "-q", "-b", "feat/spec-035")
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    calls = []
    existing = "https://github.com/acme/widgets/pull/41"
    _github_faking_run(monkeypatch, calls, existing_pr=existing)

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef", goal="continue spec 035",
        kind="implement_feature", target_branch="feat/spec-035",
    )

    assert r["committed"] is True and r["pushed"] is True and r["delivered"] is True
    # CRITICAL: stays on the pinned branch — the engineer-commit-derived
    # feat/add-the-widget-endpoint fork never happens.
    assert r["branch"] == "feat/spec-035"
    assert _branch(repo) == "feat/spec-035"
    # The pinned branch's single PR is REUSED — no `gh pr create` call at all.
    assert r["pr_url"] == existing
    assert not [c for c in calls if c[:3] == ("gh", "pr", "create")]
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", origin], capture_output=True, text=True,
    ).stdout
    assert "feat/spec-035" in refs and "feat/add-the-widget-endpoint" not in refs


async def test_deliver_base_branch_sets_pr_base_and_grounds_the_diff_range(tmp_path, monkeypatch):
    """v1-helper-resurface P1: a caller-chosen ``base_branch`` is threaded into
    BOTH halves of the seam — `gh pr create --base <it>` AND the ahead-count/
    diff base ref. The setup makes the two bases disagree: HEAD == origin/main
    exactly (fully pushed) but 1 commit ahead of origin/develop — under
    today's default base this delivery would no-op with 'no changes to
    deliver'; shipping at all proves the range used develop."""
    origin, repo = _clone_with_origin(tmp_path)
    # develop stays at the base commit; the default branch gains one more,
    # fully pushed (so ahead-of-default == 0).
    _git(repo, "branch", "develop")
    _git(repo, "push", "-q", "origin", "develop")
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")
    _git(repo, "push", "-q", "origin", "HEAD")

    calls = []
    _github_faking_run(monkeypatch, calls)

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef", goal="add the widget",
        kind="implement_feature", base_branch="develop",
    )

    assert r["delivered"] is True and r["error"] is None
    # the change was found relative to origin/develop (else: no changes) and
    # the branch derives from the commit that range surfaced.
    assert r["branch"] == "feat/add-the-widget-endpoint"
    creates = [c for c in calls if c[:3] == ("gh", "pr", "create")]
    assert len(creates) == 1
    argv = creates[0]
    assert argv[argv.index("--base") + 1] == "develop"
    assert r["pr_url"] == "https://github.com/acme/widgets/pull/7"


async def test_deliver_omitted_branch_params_keeps_legacy_behavior(tmp_path, monkeypatch):
    """v1-helper-resurface P1 (O5): with base_branch/target_branch omitted the
    delivery is byte-identical to today — a fresh engineer-derived branch, and
    `gh pr create` carries NO --base flag (GitHub defaults to the repo's
    default branch)."""
    origin, repo = _clone_with_origin(tmp_path)
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    calls = []
    _github_faking_run(monkeypatch, calls)

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef", goal="add the widget",
        kind="implement_feature",
    )

    assert r["delivered"] is True and r["pushed"] is True
    assert r["branch"] == "feat/add-the-widget-endpoint"  # fresh derived branch, as always
    creates = [c for c in calls if c[:3] == ("gh", "pr", "create")]
    assert len(creates) == 1 and "--base" not in creates[0]
    assert r["pr_url"] == "https://github.com/acme/widgets/pull/7"


async def test_deliver_failure_on_caller_chosen_branch_still_fails_closed(tmp_path, monkeypatch):
    """#183 unchanged through the seam: a push failure on a caller-pinned
    target_branch — and a pr-create failure under a caller-chosen base_branch
    — is a BROKEN delivery (`delivery_failed` truthy → the task settles
    'failed'), never a silent success."""
    from devclaw.delivery import delivery_failed

    origin, repo = _clone_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/spec-035")
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    # push breaks on the pinned branch
    _github_faking_run(monkeypatch, [], push_fails=True)
    r = await deliver_change(
        workspace_dir=repo, task_id="t1", goal="continue spec 035",
        kind="implement_feature", target_branch="feat/spec-035",
    )
    assert r["pushed"] is False and r["pr_url"] is None
    assert "push failed" in (r["error"] or "")
    assert delivery_failed(r)  # broken, not benign → the task settles failed

    # gh pr create breaks under a caller-chosen base (no existing PR to reuse)
    _github_faking_run(monkeypatch, [], create_fails=True)
    r2 = await deliver_change(
        workspace_dir=repo, task_id="t1", goal="continue spec 035",
        kind="implement_feature", target_branch="feat/spec-035", base_branch="develop",
    )
    assert r2["pr_url"] is None
    assert "gh pr create failed" in (r2["error"] or "")
    assert delivery_failed(r2)


async def test_delivered_pr_carries_devclaw_provenance_label(tmp_path, monkeypatch):
    """Machine-readable provenance: every PR devclaw opens gets the constant
    ``devclaw`` label (created-if-missing, then attached) so external reporting
    can filter shipped-by-devclaw vs shipped-by-the-human first-class — the
    PR-body signature is prose, not a filter. The scope/kind label still rides
    along in the same attach."""
    origin, repo = _clone_with_origin(tmp_path)
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    calls = []
    _github_faking_run(monkeypatch, calls)

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef", goal="add the widget",
        kind="implement_feature",
    )

    assert r["delivered"] is True and r["pr_url"]
    label_creates = [c for c in calls if c[:3] == ("gh", "label", "create")]
    assert "devclaw" in [c[3] for c in label_creates]
    edits = [c for c in calls if c[:3] == ("gh", "pr", "edit")]
    assert len(edits) == 1
    attached = edits[0][edits[0].index("--add-label") + 1].split(",")
    assert "devclaw" in attached
    assert "api" in attached  # the scope label still rides along


async def test_label_failure_never_fails_a_delivered_pr(tmp_path, monkeypatch):
    """#183 semantics unchanged by the provenance label: labelling is
    best-effort — every gh label/edit call failing leaves the delivery a full
    success (delivered, PR recorded, no error)."""
    origin, repo = _clone_with_origin(tmp_path)
    (tmp_path / "repo" / "feature.txt").write_text("agent change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add the widget endpoint")

    calls = []
    _github_faking_run(monkeypatch, calls, label_fails=True)

    r = await deliver_change(
        workspace_dir=repo, task_id="abcd1234ef", goal="add the widget",
        kind="implement_feature",
    )

    # The label calls WERE attempted and failed…
    assert any(c[:3] == ("gh", "label", "create") for c in calls)
    # …and the delivery is still a clean success.
    assert r["delivered"] is True and r["error"] is None
    assert r["pr_url"] == "https://github.com/acme/widgets/pull/7"


# ---- branch-target wire to the direct-task path (v1-helper-resurface PR-2) --


async def test_direct_task_with_target_branch_lands_on_it_end_to_end(
    store, tmp_path, monkeypatch
):
    """PR-2 wire, whole direct path: dispatch → prep puts the workspace ON the
    pinned target_branch (real prepare_workspace, created off the origin
    default since it doesn't exist yet) → the agent's change is delivered ON
    that branch (real git against a local bare origin, gh faked) → the task
    settles done with the PR recorded and the workspace still on the branch."""
    origin, repo = _clone_with_origin(tmp_path)
    calls: list = []
    _github_faking_run(monkeypatch, calls)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="continue spec 035",
        deliver=True, target_branch="feat/spec-035",
    )
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "done"
    assert t.pr_url == "https://github.com/acme/widgets/pull/7"
    # prep put the workspace on the pinned branch and delivery STAYED on it
    assert _branch(repo) == "feat/spec-035"
    import json as _json
    delivery_verdict = _json.loads(t.result_json)["delivery"]
    assert delivery_verdict["branch"] == "feat/spec-035"
    assert delivery_verdict["delivered"] is True
    # the branch (not some derived feat/* fork) reached origin
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", origin], capture_output=True, text=True,
    ).stdout
    assert "feat/spec-035" in refs


async def test_direct_task_with_unresolvable_base_branch_fails_loud_before_the_engine_runs(
    store, tmp_path, monkeypatch
):
    """PR-2 advisory (b): a bogus base_branch fails the task AT DISPATCH with
    an actionable message — the engine never runs, and no silent fresh-branch
    PR arises from downstream diff-range/PR-base skew."""
    origin, repo = _clone_with_origin(tmp_path)
    runner_calls: list = []

    async def runner(req: EngineRequest):
        runner_calls.append(req.goal)
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="add the widget",
        deliver=True, base_branch="release/9.9",
    )
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "failed"
    assert "base_branch 'release/9.9'" in (t.error or "")
    assert "does not resolve" in (t.error or "")
    assert "Push the base branch" in (t.error or "")  # actionable, not just loud
    assert runner_calls == []  # fails FAST — the agent never launched
    assert t.pr_url is None


async def test_pinned_target_branch_miss_settles_failed_not_delivered(
    store, tmp_path, monkeypatch
):
    """PR-2 advisory (a): the caller asked to CONTINUE target_branch; a
    delivery that landed anywhere else — even with a green PR — broke that
    contract and must settle 'failed', naming both branches. Settling 'done'
    would silently degrade continue-this-branch into a fresh-branch PR."""
    repo = str(tmp_path / "wsx")
    os.makedirs(repo)
    _init_repo(repo)

    async def fake_prep(workspace_dir, repo_url=None, branch=None, base_branch=None):
        return branch

    async def landed_elsewhere_deliver(**kwargs):
        return {"delivered": True, "branch": "feat/add-the-widget-endpoint",
                "committed": True, "pushed": True,
                "pr_url": "https://github.com/acme/widgets/pull/9", "error": None}

    monkeypatch.setattr("devclaw.queue.settle.prepare_workspace", fake_prep)
    monkeypatch.setattr("devclaw.queue.settle.deliver_change", landed_elsewhere_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="continue spec 035",
        deliver=True, target_branch="feat/spec-035",
    )
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "failed"
    assert "pinned target_branch 'feat/spec-035'" in (t.error or "")
    assert "feat/add-the-widget-endpoint" in (t.error or "")
    assert "fresh-branch" in (t.error or "")
    # the wrong-branch PR is named in the error for the human, not recorded as
    # this task's delivery artifact
    assert "pull/9" in (t.error or "")
    assert t.pr_url is None


async def test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape(
    store, tmp_path, monkeypatch
):
    """Goal-path/byte-unaffected pin: a task submitted WITHOUT branch params
    (exactly what the goal layer and program children do) triggers no prep
    subprocess and calls deliver_change with the LEGACY kwarg shape — a
    pre-PR-2 test stub signature (no base_branch/target_branch) still works."""
    repo = str(tmp_path / "wsy")
    os.makedirs(repo)
    _init_repo(repo)
    prep_calls: list = []

    async def fake_prep(workspace_dir, repo_url=None, branch=None, base_branch=None):
        prep_calls.append(branch)
        return branch

    # The BRANCH-TARGET kwargs must stay absent for a task that pinned neither
    # (v1-helper PR-2's blank-safe wire). ``judged_head``/``agent_authored`` are
    # not branch params — they ride EVERY materialized delivery (spec 013) — so
    # they are captured and asserted separately below.
    seen_kwargs: dict = {}

    async def legacy_deliver(*, workspace_dir, task_id, goal, kind=None,
                             verify=None, title=None, advisories=None, **kw):
        seen_kwargs.update(kw)
        return {"delivered": True, "branch": "devclaw/x", "committed": True,
                "pushed": True, "pr_url": "https://github.com/acme/w/pull/3",
                "error": None}

    monkeypatch.setattr("devclaw.queue.settle.prepare_workspace", fake_prep)
    monkeypatch.setattr("devclaw.queue.settle.deliver_change", legacy_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="add feature",
        deliver=True,
    )
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "done"
    assert t.pr_url == "https://github.com/acme/w/pull/3"
    assert prep_calls == []  # no branch params → the wire is fully inert
    assert "base_branch" not in seen_kwargs and "target_branch" not in seen_kwargs


async def test_target_branch_on_base_or_default_is_rejected_before_any_push(
    store, tmp_path, monkeypatch
):
    """Invariant-guard finding on PR-2: target_branch == base_branch (or the
    remote default) would put the workspace ON the base itself and delivery's
    branch-reuse mode would push unreviewed commits STRAIGHT to it, failing
    only afterwards on `gh pr create` — loud but already irreversible. The
    contract is rejected at prep: the engine never runs, prepare_workspace is
    never called, nothing is ever pushed."""
    origin, repo = _clone_with_origin(tmp_path)
    runner_calls: list = []
    prep_calls: list = []

    async def runner(req: EngineRequest):
        runner_calls.append(req.goal)
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    async def recording_prep(*a, **kw):
        prep_calls.append((a, kw))

    monkeypatch.setattr("devclaw.queue.settle.prepare_workspace", recording_prep)

    q = TaskQueue(store, runner=runner)

    # target == remote default (main): rejected.
    tid = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="tweak on main",
        deliver=True, target_branch="main",
    )
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"
    assert "default branch" in (t.error or "")
    assert "never pushes" in (t.error or "")

    # target == base (non-default): rejected before the base is even fetched.
    tid2 = q.submit(
        kind="implement_feature", workspace_dir=repo, goal="tweak on release",
        deliver=True, base_branch="release/1.0", target_branch="release/1.0",
    )
    await q.drain()
    t2 = store.get_task(tid2)
    assert t2.status == "failed"
    assert "equals base_branch" in (t2.error or "")

    assert runner_calls == []   # the agent never launched for either
    assert prep_calls == []     # the workspace was never put on the base
    # And the real origin's main is untouched — no push ever happened.
    out = subprocess.run(
        ["git", "rev-parse", "main"], cwd=origin, capture_output=True, text=True,
    )
    assert out.returncode == 0


# ---- atomic sweep + goal-PR refresh (ledger PR #4 warts) -------------------


async def test_deliver_amends_stray_files_into_the_worker_commit_atomically(tmp_path):
    """The two-commit fixup bug (ledger PR #4): the worker commits its milestone,
    then a leftover file (a lockfile the gate regenerated, a scaffold dotfile it
    forgot) is present uncommitted. The sweep must FOLD into the worker's own
    commit — one atomic commit, no second commit borrowing the worker's headline."""
    origin, repo = _clone_with_origin(tmp_path)
    (tmp_path / "repo" / "app.ts").write_text("// the app\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(frontend): scaffold the app (M8)")
    # a generated leftover, uncommitted (e.g. package-lock.json the gate rewrote)
    (tmp_path / "repo" / "package-lock.json").write_text("{}\n")

    r = await deliver_change(
        workspace_dir=repo, task_id="dead00beef",
        goal="build the frontend", kind="implement_feature",
    )

    assert r["committed"] is True and r["delivered"] is True
    n = subprocess.run(
        ["git", "-C", repo, "rev-list", "--count", "origin/main..HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert n == "1", f"expected one atomic commit, got {n}"
    files = subprocess.run(
        ["git", "-C", repo, "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True,
    ).stdout
    assert "package-lock.json" in files and "app.ts" in files
    subj = subprocess.run(
        ["git", "-C", repo, "log", "--format=%s", "origin/main..HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert subj == "feat(frontend): scaffold the app (M8)"


async def test_deliver_never_amends_an_already_pushed_goal_increment(tmp_path):
    """Safety: on a goal branch whose prior increment is already pushed, a run
    that leaves the tree dirty WITHOUT a new worker commit must NOT amend the
    pushed commit (that needs a force-push) — it makes a fresh commit. Guards the
    _head_is_pushed branch of the sweep."""
    origin, repo = _clone_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "goal/g1")
    (tmp_path / "repo" / "m1.txt").write_text("m1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: increment one")
    _git(repo, "push", "-q", "-u", "origin", "goal/g1")
    pushed_sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    # this run leaves the tree dirty but makes NO new commit.
    (tmp_path / "repo" / "stray.txt").write_text("stray\n")

    r = await deliver_change(
        workspace_dir=repo, task_id="cafef00d99",
        goal="continue g1", kind="implement_feature",
    )

    assert r["committed"] is True and r["delivered"] is True and r["error"] is None
    parent = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD~1"], capture_output=True, text=True
    ).stdout.strip()
    assert parent == pushed_sha, "the pushed increment must not be rewritten"
    files = subprocess.run(
        ["git", "-C", repo, "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True,
    ).stdout
    assert "stray.txt" in files


async def test_deliver_goal_branch_refreshes_the_existing_pr_to_accumulated_state(tmp_path, monkeypatch):
    """The stale-goal-PR bug (ledger PR #4 kept M1's title over 8 commits): on a
    goal branch whose PR already exists, delivery must REFRESH the PR title
    (goal-level) and body (the running increment list) via `gh pr edit`, not
    return the frozen first-increment title."""
    origin, repo = _clone_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "goal/ledger")
    (tmp_path / "repo" / "m1.txt").write_text("m1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "feat(backend): scaffold (M1)")
    (tmp_path / "repo" / "m2.txt").write_text("m2\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "feat(backend): persistence (M2)")

    calls = []
    existing = "https://github.com/acme/widgets/pull/4"
    _github_faking_run(monkeypatch, calls, existing_pr=existing)

    r = await deliver_change(
        workspace_dir=repo, task_id="0ddba11abc",
        goal="Build Ledger, a full-stack expense tracker", kind="implement_feature",
    )

    assert r["pr_url"] == existing and r["delivered"] is True
    assert not [c for c in calls if c[:3] == ("gh", "pr", "create")]
    edits = [c for c in calls if c[:3] == ("gh", "pr", "edit")]
    assert edits, "expected a gh pr edit refresh call"
    edit = edits[0]
    ti = edit[edit.index("--title") + 1]
    assert "Ledger" in ti and "M1" not in ti
    body = edit[edit.index("--body") + 1]
    assert "Increments landed" in body and "(M1)" in body and "(M2)" in body


async def test_goal_branch_pr_title_prefers_worker_commit_subject(tmp_path, monkeypatch):
    """The S1 run-001 live find (#551): a one-slice goal-branch PR was titled
    from the objective (`feat: Implement intake issue #2 of dsdevq/…: a…`) even
    though the worker wrote a perfectly good commit subject — the goal-branch
    path never consulted commit subjects. Same class as _resolve_title: while a
    single increment sits on the branch, the worker's own subject IS the PR
    description and must title it. (Once more increments land, the goal-level
    re-title takes over — test_deliver_goal_branch_refreshes… guards that.)"""
    origin, repo = _clone_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "goal/s1-001")
    (tmp_path / "repo" / "notes.py").write_text("api\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(api): add a minimal field notes endpoint")

    calls = []
    _github_faking_run(monkeypatch, calls)

    goal = (
        "Implement intake issue #2 of dsdevq/devclaw-shakedown-s1-001: "
        "a minimal field notes REST API with create, list, and delete endpoints"
    )
    r = await deliver_change(
        workspace_dir=repo, task_id="s1deadbeef", goal=goal, kind="implement_feature",
    )

    assert r["delivered"] is True and r["pr_url"]
    create = next(c for c in calls if c[:3] == ("gh", "pr", "create"))
    title = create[create.index("--title") + 1]
    assert title == "feat(api): add a minimal field notes endpoint"
    assert "Implement intake issue" not in title
    # the issue ref lands where it belongs — the body's Closes line
    body = create[create.index("--body") + 1]
    assert "Closes #2" in body
