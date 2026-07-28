"""Workspace prep — the clone/fetch/reset lifecycle that hands the engine a
pristine checkout. These cover the *failure* messages specifically: they become
a goal's ``blocked_on`` and are read by the (non-technical) owner, so they must
say what's wrong and what to do — not leak a bare assertion."""

from __future__ import annotations

import pytest

from devclaw.engine.workspace import WorkspaceError, prepare_workspace


@pytest.mark.asyncio
async def test_no_repo_url_message_asks_the_owner(tmp_path):
    """A workspace that isn't a git repo and has no repo_url must raise an
    actionable ask (Fix 2): name the missing repo_url AND make clear we won't
    silently `git init`. This is the text the owner sees as blocked_on."""
    empty = tmp_path / "fresh"
    empty.mkdir()

    with pytest.raises(WorkspaceError) as ei:
        await prepare_workspace(str(empty), repo_url=None)

    msg = str(ei.value)
    assert "repo_url" in msg
    assert "git init" in msg


@pytest.mark.asyncio
async def test_bad_repo_url_surfaces_git_error(tmp_path):
    """A clone that fails carries the real git stderr through, so blocked_on shows
    the owner *why* (e.g. 'Repository not found') rather than a generic failure."""
    dest = tmp_path / "wont-clone"

    with pytest.raises(WorkspaceError) as ei:
        await prepare_workspace(
            str(dest),
            repo_url="https://github.com/dsdevq/this-repo-does-not-exist-xyz",
        )

    assert "clone failed" in str(ei.value)


# ---- goal-branch mode (Pillar 2) ------------------------------------------
#
# Spin up a bare local "origin" + a working clone of it so we can exercise
# the real prepare_workspace against a real git tree — no network, no GitHub.

import subprocess


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _git_out(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_origin_with_main(tmp_path):
    """A bare repo on disk with a non-empty ``main`` branch. Returns its path."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, capture_output=True,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed commit")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    return str(origin)


@pytest.mark.asyncio
async def test_no_branch_arg_keeps_legacy_default_branch_reset(tmp_path):
    """Backwards-compat: prepare_workspace called WITHOUT branch must still
    return to the default branch + reset to its tip — the legacy mode the
    goal layer relies on for discovery + done-gate stays intact."""
    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"

    out = await prepare_workspace(str(ws), repo_url=origin)
    assert out == "main"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "main"


@pytest.mark.asyncio
async def test_goal_branch_created_from_main_when_missing(tmp_path):
    """First item of a checklist goal: ``goal/<id>`` doesn't exist anywhere
    → prepare_workspace creates it locally off the default branch."""
    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"

    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/my-goal")
    assert out == "goal/my-goal"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "goal/my-goal"
    assert (ws / "README.md").read_text() == "seed\n"


@pytest.mark.asyncio
async def test_goal_branch_resets_to_remote_tip_when_already_exists(tmp_path):
    """Second + Nth item: branch ``goal/<id>`` already exists on origin (a
    prior item pushed). prepare_workspace fetches + resets to
    ``origin/goal/<id>`` so the workspace sees ALL prior items' commits."""
    origin = _make_origin_with_main(tmp_path)

    pre = tmp_path / "pre"
    subprocess.run(["git", "clone", origin, str(pre)], check=True, capture_output=True)
    _git(pre, "checkout", "-b", "goal/my-goal")
    (pre / "item-1.txt").write_text("first item")
    _git(pre, "add", "-A"); _git(pre, "commit", "-m", "scaffold")
    (pre / "item-2.txt").write_text("second item")
    _git(pre, "add", "-A"); _git(pre, "commit", "-m", "abstractions")
    _git(pre, "push", "-u", "origin", "goal/my-goal")

    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/my-goal")
    assert out == "goal/my-goal"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "goal/my-goal"
    assert (ws / "item-1.txt").read_text() == "first item"
    assert (ws / "item-2.txt").read_text() == "second item"


@pytest.mark.asyncio
async def test_goal_branch_resets_local_changes_to_remote(tmp_path):
    """Pillar 2 invariant: workspace state from a prior task (local-only
    commits + uncommitted debris) must NOT survive into the next item's
    prep — only what's on ``origin/goal/<id>`` counts."""
    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"

    await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")
    (ws / "shipped.txt").write_text("would have shipped")
    _git(ws, "add", "-A"); _git(ws, "commit", "-m", "fake shipped")
    (ws / "junk.txt").write_text("uncommitted debris")

    push_helper = tmp_path / "push"
    subprocess.run(
        ["git", "clone", "-b", "main", origin, str(push_helper)],
        check=True, capture_output=True,
    )
    _git(push_helper, "checkout", "-b", "goal/g")
    (push_helper / "shipped.txt").write_text("would have shipped")
    _git(push_helper, "add", "-A"); _git(push_helper, "commit", "-m", "real shipped")
    _git(push_helper, "push", "-u", "origin", "goal/g")

    await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")
    assert (ws / "shipped.txt").read_text() == "would have shipped"
    assert not (ws / "junk.txt").exists()


@pytest.mark.asyncio
async def test_branch_equals_default_branch_uses_legacy_path(tmp_path):
    """Backwards-compat edge: explicitly passing the default branch name
    behaves like the legacy no-branch path — same hard-reset to origin/main."""
    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="main")
    assert out == "main"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "main"


@pytest.mark.asyncio
async def test_goal_branch_prep_survives_dirty_tracked_file(tmp_path):
    """The 2026-07-25 closeloop-bench wedge: a TRACKED ``.devclaw/trends.md``
    (committed to the goal branch by a prior action) gets appended to by the
    trend detector between actions, and an unforced ``checkout -B`` then
    refuses with 'Your local changes … would be overwritten by checkout' —
    a mechanical:prep block caused by the loop's own mechanism output. Prep
    must force through: local modifications to tracked files never survive,
    and never wedge the goal."""
    origin = _make_origin_with_main(tmp_path)

    # A prior action committed .devclaw/trends.md to the goal branch.
    pre = tmp_path / "pre"
    subprocess.run(["git", "clone", origin, str(pre)], check=True, capture_output=True)
    _git(pre, "checkout", "-b", "goal/g")
    (pre / ".devclaw").mkdir()
    (pre / ".devclaw" / "trends.md").write_text("## trends\n")
    _git(pre, "add", "-A"); _git(pre, "commit", "-m", "commit trends file")
    _git(pre, "push", "-u", "origin", "goal/g")

    ws = tmp_path / "ws"
    await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")

    # Between actions the trend detector appends to the now-tracked file.
    (ws / ".devclaw" / "trends.md").write_text("## trends\n- new observation\n")

    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")
    assert out == "goal/g"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "goal/g"
    # Pristine at the remote tip — the local append is discarded, not wedging.
    assert (ws / ".devclaw" / "trends.md").read_text() == "## trends\n"
    assert _git_out(ws, "status", "--porcelain") == ""


# ---- #394 done-when 2: re-seed the goal branch after its PR merged ----------
#
# Git alone can't see a squash-merge (the squashed commit is patch-equivalent
# to nothing on the branch), so prepare_workspace asks GitHub for the branch's
# most recent MERGED PR head via the module-global _merged_pr_head — patched
# here to simulate what gh would say. The file-remote gate inside it keeps
# every other test in this file at zero gh subprocesses.


def _seed_goal_branch(tmp_path, origin, files: dict[str, str]):
    """Push ``goal/g`` to origin carrying one commit per file. Returns the
    per-commit SHAs in order."""
    pre = tmp_path / "pre"
    subprocess.run(["git", "clone", origin, str(pre)], check=True, capture_output=True)
    _git(pre, "checkout", "-b", "goal/g")
    shas = []
    for name, content in files.items():
        (pre / name).write_text(content)
        _git(pre, "add", "-A"); _git(pre, "commit", "-m", f"add {name}")
        shas.append(_git_out(pre, "rev-parse", "HEAD"))
    _git(pre, "push", "-u", "origin", "goal/g")
    return shas


def _squash_onto_main(tmp_path, origin, files: dict[str, str], clone_name="squasher"):
    """Simulate the owner squash-merging: land ``files`` on main as ONE commit."""
    helper = tmp_path / clone_name
    subprocess.run(
        ["git", "clone", "-b", "main", origin, str(helper)],
        check=True, capture_output=True,
    )
    for name, content in files.items():
        (helper / name).write_text(content)
    _git(helper, "add", "-A"); _git(helper, "commit", "-m", "squash-merge goal/g PR")
    _git(helper, "push", "origin", "main")
    return helper


@pytest.mark.asyncio
async def test_fully_merged_goal_branch_is_reseeded_from_default(tmp_path, monkeypatch):
    """#394 done-when 2, the closeloop-bench PR #8 class: after the goal
    branch's PR is squash-merged (branch NOT deleted), the next prep must NOT
    stack delivery N+1 onto the spent branch — it re-seeds from the default
    tip and clears the spent remote branch (the end state auto-merge's
    --delete-branch leaves), so the next delivery starts landable."""
    origin = _make_origin_with_main(tmp_path)
    files = {"item-1.txt": "first", "item-2.txt": "second"}
    shas = _seed_goal_branch(tmp_path, origin, files)
    _squash_onto_main(tmp_path, origin, files)

    async def fake_merged_head(workspace_dir, branch, base_branch):
        return shas[-1]  # gh: the merged PR's head == the branch tip

    monkeypatch.setattr("devclaw.engine.workspace._merged_pr_head", fake_merged_head)
    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")

    assert out == "goal/g"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "goal/g"
    # Re-seeded AT the default tip — the squashed content is here, the spent
    # pre-squash commits are not.
    assert _git_out(ws, "rev-parse", "HEAD") == _git_out(ws, "rev-parse", "origin/main")
    assert (ws / "item-1.txt").read_text() == "first"
    # The spent remote branch is gone.
    assert _git_out(ws, "ls-remote", "origin", "refs/heads/goal/g") == ""


@pytest.mark.asyncio
async def test_post_merge_commits_are_rebased_onto_default(tmp_path, monkeypatch):
    """A delivery that landed on the goal branch AFTER its PR merged is real,
    unmerged work — prep must carry it over: rebase only the post-merge
    commits onto the default tip and update the remote to match (delivery
    pushes plain, so a local-only rebase would break the next push)."""
    origin = _make_origin_with_main(tmp_path)
    shas = _seed_goal_branch(
        tmp_path, origin, {"item-1.txt": "first", "item-2.txt": "second"}
    )
    _squash_onto_main(tmp_path, origin, {"item-1.txt": "first"})  # PR = commit 1 only

    async def fake_merged_head(workspace_dir, branch, base_branch):
        return shas[0]  # gh: the merged PR's head is one commit behind the tip

    monkeypatch.setattr("devclaw.engine.workspace._merged_pr_head", fake_merged_head)
    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")

    assert out == "goal/g"
    # Both files present: item-1 via main's squash commit, item-2 via the
    # rebased post-merge commit…
    assert (ws / "item-1.txt").read_text() == "first"
    assert (ws / "item-2.txt").read_text() == "second"
    # …and the branch is exactly ONE commit ahead of the default tip.
    assert _git_out(ws, "rev-list", "--count", "origin/main..HEAD") == "1"
    # The remote was force-with-lease'd to the rebased tip — the next plain
    # delivery push fast-forwards.
    remote_tip = _git_out(ws, "ls-remote", "origin", "refs/heads/goal/g").split()[0]
    assert remote_tip == _git_out(ws, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_reseed_conflict_falls_back_to_remote_tip(tmp_path, monkeypatch):
    """Conservative by construction: when the post-merge commits can't be
    rebased cleanly (the default branch moved in a conflicting way), prep
    must NOT leave a half-rebased tree or a mutated remote — it falls back to
    today's reset-to-remote-tip; the settle-time mergeability probe (#394
    PR 1) is what makes the resulting conflict loud."""
    origin = _make_origin_with_main(tmp_path)
    shas = _seed_goal_branch(
        tmp_path, origin,
        {"README.md": "goal version A\n", "conflict.txt": "goal version B\n"},
    )
    # Owner squashed commit 1, then main moved AGAIN touching the same file
    # the post-merge commit edits — the replay of commit 2 now conflicts.
    _squash_onto_main(
        tmp_path, origin, {"README.md": "goal version A\n", "conflict.txt": "seeded\n"},
    )
    _squash_onto_main(
        tmp_path, origin, {"conflict.txt": "main hotfix\n"}, clone_name="hotfixer",
    )

    async def fake_merged_head(workspace_dir, branch, base_branch):
        return shas[0]

    monkeypatch.setattr("devclaw.engine.workspace._merged_pr_head", fake_merged_head)
    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")

    assert out == "goal/g"
    # Exactly today's behavior: workspace at the remote tip, clean, no rebase
    # residue; the remote branch untouched.
    assert _git_out(ws, "rev-parse", "HEAD") == shas[-1]
    assert _git_out(ws, "status", "--porcelain") == ""
    remote_tip = _git_out(ws, "ls-remote", "origin", "refs/heads/goal/g").split()[0]
    assert remote_tip == shas[-1]


@pytest.mark.asyncio
async def test_human_target_branch_is_never_reseeded(tmp_path, monkeypatch):
    """Blast-radius scope (invariant-guard find): the re-seed deletes and
    force-pushes REMOTE branches, and devclaw is sole writer only of its own
    ``goal/<id>`` namespace. The ADR 0011 direct-task path routes arbitrary
    HUMAN-owned ``target_branch`` names through the same prep — a merged-then-
    reused feature branch there must behave exactly as today (reset to remote
    tip), never be deleted or history-rewritten, even when gh WOULD report a
    merged PR for it."""
    origin = _make_origin_with_main(tmp_path)
    pre = tmp_path / "pre"
    subprocess.run(["git", "clone", origin, str(pre)], check=True, capture_output=True)
    _git(pre, "checkout", "-b", "feat/payment")
    (pre / "pay.txt").write_text("human work")
    _git(pre, "add", "-A"); _git(pre, "commit", "-m", "human commit")
    _git(pre, "push", "-u", "origin", "feat/payment")
    tip = _git_out(pre, "rev-parse", "HEAD")

    async def must_not_be_called(workspace_dir, branch, base_branch):  # pragma: no cover
        raise AssertionError("merged-PR lookup must not run for a non-goal/ branch")

    monkeypatch.setattr("devclaw.engine.workspace._merged_pr_head", must_not_be_called)
    ws = tmp_path / "ws"
    out = await prepare_workspace(str(ws), repo_url=origin, branch="feat/payment")

    assert out == "feat/payment"
    assert _git_out(ws, "rev-parse", "HEAD") == tip
    remote_tip = _git_out(ws, "ls-remote", "origin", "refs/heads/feat/payment").split()[0]
    assert remote_tip == tip


@pytest.mark.asyncio
async def test_merged_pr_lookup_skips_non_github_remotes(tmp_path):
    """The gh call is gated on a github.com origin — a local/file remote (every
    fixture in this file, and any non-GitHub deployment) must resolve to None
    without ever spawning gh."""
    from devclaw.engine.workspace import _merged_pr_head

    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"
    await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")

    assert await _merged_pr_head(str(ws), "goal/g", "main") is None


@pytest.mark.asyncio
async def test_goal_branch_create_survives_dirty_tracked_file(tmp_path):
    """Same wedge class on the FIRST-item path: the goal branch doesn't exist
    on origin yet, but a tracked file (committed on main) was modified locally
    between preps. Creating ``goal/<id>`` off origin/main must force through
    the dirt, not refuse."""
    origin = _make_origin_with_main(tmp_path)
    ws = tmp_path / "ws"

    await prepare_workspace(str(ws), repo_url=origin)  # clone at main
    (ws / "README.md").write_text("locally dirtied tracked file\n")

    out = await prepare_workspace(str(ws), repo_url=origin, branch="goal/g")
    assert out == "goal/g"
    assert _git_out(ws, "rev-parse", "--abbrev-ref", "HEAD") == "goal/g"
    assert (ws / "README.md").read_text() == "seed\n"
    assert _git_out(ws, "status", "--porcelain") == ""
