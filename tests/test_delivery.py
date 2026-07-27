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
    _extract_pr_url,
    _pr_body,
    _pr_title,
    _scope_suffix,
    _slug,
    deliver_change,
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


def test_pr_body_carries_ticket_gate_and_caveat():
    verify = {"ran": True, "cmd": "dotnet test", "passed": True, "exit_code": 0}
    body = _pr_body("Add an endpoint", "abcd1234", verify, " Program.cs | 6 +\n 1 file changed")
    assert "## What" in body and "Add an endpoint" in body
    assert "Gate `dotnet test` passed" in body
    assert "## Files changed" in body and "Program.cs" in body
    assert "review before merging" in body.lower()  # the honest caveat
    # degrades cleanly when there was no gate
    nogate = _pr_body("x", "id", None, None)
    assert "## Verification" not in nogate and "## Files changed" not in nogate


def test_pr_body_renders_trust_advisory_section():
    # ADR 0007: a trust-mode gate advisory rides into the PR body so the human
    # sees it at the merge boundary. Absent when there are no advisories.
    advisories = [{"gate": "review", "reason": "dead code left in DealService"}]
    body = _pr_body("Add X", "id", None, None, advisories=advisories)
    assert "Advisory" in body and "shipped under `trust`" in body
    assert "review" in body and "dead code left in DealService" in body
    assert "review before merging" in body.lower()
    # no advisory section when the list is empty/None
    assert "Advisory" not in _pr_body("Add X", "id", None, None)


def test_scope_suffix_empty_or_missing():
    # No files_stat → no suffix. Graceful on the None/"" edges.
    assert _scope_suffix(None) == ""
    assert _scope_suffix("") == ""
    # A malformed stat with no `files changed` line — return "" rather than raise.
    assert _scope_suffix("some garbage output") == ""


def test_scope_suffix_narrow_diff_no_fire():
    # Narrow — 2 files, 30 lines — well under both thresholds → no suffix.
    stat = " foo.py | 20 ++++++++++++++++++++\n bar.py | 10 ++++++++++\n 2 files changed, 30 insertions(+), 0 deletions(-)"
    assert _scope_suffix(stat) == ""


def test_scope_suffix_wide_by_files_fires():
    # 6 files with a small line count → fires because file-count crosses.
    stat = (
        " a.py | 5 ++++-\n b.py | 5 ++++-\n c.py | 5 ++++-\n"
        " d.py | 5 ++++-\n e.py | 5 ++++-\n f.py | 5 ++++-\n"
        " 6 files changed, 24 insertions(+), 6 deletions(-)"
    )
    suf = _scope_suffix(stat)
    assert suf.startswith(" (spans 6 files")
    assert "30 lines)" in suf  # 24 + 6 = 30, below 1k threshold → raw int


def test_scope_suffix_wide_by_lines_fires():
    # 2 files but 1800 lines → fires because line-count crosses.
    stat = " App.tsx | 1800 +++++++...\n types.ts | 200 ++++++...\n 2 files changed, 1800 insertions(+), 200 deletions(-)"
    suf = _scope_suffix(stat)
    assert suf.startswith(" (spans 2 files")
    assert "2.0k lines)" in suf


def test_scope_suffix_the_closeloop_pr_23_case():
    # The concrete regression this fix targets: closeloop PR #23 restructure —
    # ~10 feature-dir files, ~1800 net insertions + ~1600 deletions.
    stat = (
        " frontend/src/App.tsx | 1650 -----\n"
        " frontend/src/features/accounts/AccountsView.tsx | 320 +++++\n"
        " frontend/src/features/activities/ActivitiesView.tsx | 280 +++\n"
        " frontend/src/features/auth/LoginView.tsx | 90 +++\n"
        " frontend/src/features/contacts/ContactsView.tsx | 360 +++\n"
        " frontend/src/features/pipeline/PipelineView.tsx | 410 +++\n"
        " frontend/src/features/stats/StatsView.tsx | 120 +++\n"
        " frontend/src/features/today/TodayView.tsx | 130 +++\n"
        " frontend/src/hooks/useAppState.ts | 145 +++\n"
        " frontend/src/types.ts | 30 +++\n"
        " 10 files changed, 1885 insertions(+), 1650 deletions(-)"
    )
    suf = _scope_suffix(stat)
    assert suf.startswith(" (spans 10 files")
    assert "3.5k lines)" in suf  # 1885 + 1650 = 3535, formatted as 3.5k


def test_scope_suffix_tunable_thresholds():
    # Callers can dial the thresholds tighter or looser.
    tight_stat = " foo.py | 4 +++-\n bar.py | 4 +++-\n baz.py | 4 +++-\n 3 files changed, 12 insertions(+), 0 deletions(-)"
    assert _scope_suffix(tight_stat) == ""  # default: no fire
    assert _scope_suffix(tight_stat, min_files=3) != ""  # tightened: fires


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
    assert r["branch"] == "devclaw/abcd1234-add-new-file"
    assert r["pushed"] is False and r["pr_url"] is None
    assert r["delivered"] is True
    assert "no 'origin' remote" in r["error"]
    assert _branch(repo) == "devclaw/abcd1234-add-new-file"  # change is on the branch


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
    # the changes-path body leads with what changed + collapses the ticket
    body = _pr_body("the long ticket instruction", "id", None, None, changes="Added a widget endpoint + tests")
    assert "## Changes" in body and "Added a widget endpoint" in body
    assert "Ticket" in body and "the long ticket instruction" in body
    assert "## What" not in body  # the instruction is the ticket, not the headline


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

    async def fake_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None, title=None, advisories=None):
        # While delivery runs, the task must still be 'running' (not yet 'done').
        seen["status_during_delivery"] = store.get_task(task_id).status
        seen["pr_url_during_delivery"] = store.get_task(task_id).pr_url
        return {"delivered": True, "pr_url": pr, "branch": "devclaw/x", "pushed": True}

    monkeypatch.setattr("devclaw.task_queue.deliver_change", fake_deliver)

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

    async def broken_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None, title=None, advisories=None):
        return {"delivered": True, "branch": "devclaw/x", "committed": True,
                "pushed": False, "pr_url": None,
                "error": "push failed (check repo push auth): remote rejected"}

    monkeypatch.setattr("devclaw.task_queue.deliver_change", broken_deliver)

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

    async def raising_deliver(*, workspace_dir, task_id, goal, kind=None, verify=None, title=None, advisories=None):
        raise RuntimeError("gh exploded")

    monkeypatch.setattr("devclaw.task_queue.deliver_change", raising_deliver)

    q = TaskQueue(store, runner=_writing_runner("feature.txt"))
    tid = q.submit(kind="implement_feature", workspace_dir=repo, goal="add feature", deliver=True)
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "failed"
    assert "gh exploded" in (t.error or "")


async def test_no_changes_delivery_still_settles_done(store, tmp_path):
    """Benign no-PR outcome: the gate passed but the workspace has no changes
    (e.g. the requirement already held). Nothing was shipped because nothing
    existed to ship — that is a 'done' without a PR, not a failure. (The
    no-remote sibling case is covered by test_open_pr_task_triggers_delivery.)"""
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
    # the delivery verdict rides along in the persisted result as evidence
    import json as _json
    assert "no changes to deliver" in _json.loads(t.result_json)["delivery"]["error"]


# ---- branch-target delivery seam (v1-helper-resurface P1) -------------------


def _clone_with_origin(tmp_path):
    """The bare-origin + clone fixture the engineer-commit tests use, factored:
    a local bare origin, a configured clone, one 'base' commit pushed."""
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
    return origin, repo


def _github_faking_run(monkeypatch, calls, *, existing_pr=None, push_fails=False,
                       create_fails=False):
    """Wrap delivery._run: git stays REAL (real branches, real pushes to the
    local bare origin), but the remote LOOKS like GitHub and ``gh`` is faked —
    the existing tests never reach the gh path, and this is the only seam that
    lets a test observe the ``gh pr create`` argv."""
    real_run = getattr(delivery._run, "_devclaw_real", delivery._run)

    async def fake_run(prog, *args, cwd):
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
        return await real_run(prog, *args, cwd=cwd)

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

    monkeypatch.setattr("devclaw.task_queue.prepare_workspace", fake_prep)
    monkeypatch.setattr("devclaw.task_queue.deliver_change", landed_elsewhere_deliver)

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

    # Deliberately the OLD signature: extra kwargs would raise TypeError here.
    async def legacy_deliver(*, workspace_dir, task_id, goal, kind=None,
                             verify=None, title=None, advisories=None):
        return {"delivered": True, "branch": "devclaw/x", "committed": True,
                "pushed": True, "pr_url": "https://github.com/acme/w/pull/3",
                "error": None}

    monkeypatch.setattr("devclaw.task_queue.prepare_workspace", fake_prep)
    monkeypatch.setattr("devclaw.task_queue.deliver_change", legacy_deliver)

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

    monkeypatch.setattr("devclaw.task_queue.prepare_workspace", recording_prep)

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
