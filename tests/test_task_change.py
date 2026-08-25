"""The ONE definition of the change (spec 013, #630).

Two components used to compute "what did the agent change?" independently.
Delivery staged everything and could not miss a file; the gates diffed the
working tree against the pre-run ref and saw only what the agent chose to
RECORD — and what made it record was a sentence in a worker skill. Live on
2026-08-22 (task 78201bce, dsdevq/devclaw-shakedown-598) delivery shipped 4
files / +179 lines while the gates judged 1 file / +32: three new files created
and never recorded, so a change made entirely of new unrecorded files reached
every gate as an EMPTY span and passed them trivially.

Real git throughout — materialization IS a git operation, and a stubbed one
would prove nothing.
"""

from __future__ import annotations

import subprocess

import pytest

from devclaw import task_queue
from devclaw.queue import settle as queue_settle
from devclaw.task_change import (
    CHANGE,
    ERROR,
    MACHINE_COMMIT_SUBJECT,
    NO_CHANGE,
    NO_REPO,
    materialization_message,
    materialize_worktree_sync,
)


def _git(d, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(d), *args], capture_output=True, text=True, check=check,
    )


def _repo(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(d, *args)
    (d / "README.md").write_text("# base\n")
    (d / ".gitignore").write_text("*.log\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base")
    return d, _git(d, "rev-parse", "HEAD").stdout.strip()


async def _capture(d, base, *, task_id="t1"):
    return await task_queue._capture_change(
        str(d), base, task_id=task_id, message=materialization_message(task_id),
    )


# ---- the headline defect ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_change_made_only_of_files_the_agent_never_recorded_is_judged_in_full(
    tmp_path,
):
    """SC-002/SC-003, the measured 2026-08-22 shape: three new files the agent
    never recorded plus one modified tracked file. Before, the judged span was
    the one modified file; now it is all four, and the span is non-empty for a
    change made entirely of new files."""
    d, base = _repo(tmp_path)
    (d / "README.md").write_text("# base\nedited\n")          # tracked, modified
    (d / "AGENTS.md").write_text("A\n" * 41)                   # new, unrecorded
    (d / "ARCHITECTURE.md").write_text("B\n" * 94)             # new, unrecorded
    (d / ".devcontainer").mkdir()
    (d / ".devcontainer" / "Dockerfile").write_text("C\n" * 12)  # new, unrecorded

    change = await _capture(d, base)

    assert change.status == CHANGE
    for path in ("README.md", "AGENTS.md", "ARCHITECTURE.md",
                 ".devcontainer/Dockerfile"):
        assert path in change.diff, f"{path} missing from the judged span"
    stats = task_queue._diff_stats(change.diff)
    assert stats["files"] == 4
    assert stats["insertions"] == 41 + 94 + 12 + 1


@pytest.mark.asyncio
async def test_a_change_of_only_new_unrecorded_files_is_never_an_empty_span(tmp_path):
    """SC-003 on its own: the pathological case is the one made ENTIRELY of new
    files, because that reached the gates as nothing at all."""
    d, base = _repo(tmp_path)
    (d / "brand_new.py").write_text("X = 1\n")

    change = await _capture(d, base)
    assert change.status == CHANGE and "brand_new.py" in change.diff


# ---- the cases that must not change ---------------------------------------


@pytest.mark.asyncio
async def test_a_worker_that_recorded_all_of_its_work_gets_no_extra_commit(tmp_path):
    """FR-008: same history, same message, no additional commit."""
    d, base = _repo(tmp_path)
    (d / "feature.py").write_text("F = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "feat: the worker's own subject")
    before = _git(d, "rev-parse", "HEAD").stdout.strip()
    log_before = _git(d, "log", "--format=%s").stdout

    change = await _capture(d, base)

    assert change.status == CHANGE
    assert change.head_sha == before          # no new commit
    assert change.materialized is False
    assert change.agent_authored is True
    assert _git(d, "log", "--format=%s").stdout == log_before
    assert "feature.py" in change.diff


@pytest.mark.asyncio
async def test_a_partly_recorded_change_is_judged_whole_and_folded_into_one_commit(
    tmp_path,
):
    """FR-001 + the amend rule delivery already applied to the stray-lockfile
    case, applied to the whole class: the worker's own subject survives, and the
    leftover it forgot is inside the same commit rather than a second one."""
    d, base = _repo(tmp_path)
    (d / "recorded.py").write_text("R = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "feat: recorded half")
    (d / "left_behind.py").write_text("L = 1\n")

    change = await _capture(d, base)

    assert change.status == CHANGE and change.materialized is True
    assert "recorded.py" in change.diff and "left_behind.py" in change.diff
    assert _git(d, "log", "--format=%s", f"{base}..HEAD").stdout.strip() == (
        "feat: recorded half"
    )


@pytest.mark.asyncio
async def test_files_the_repository_ignores_are_excluded_from_the_judged_span(tmp_path):
    """The two views must agree on EXCLUSIONS too: an ignored file is not
    published, so it must not be judged."""
    d, base = _repo(tmp_path)
    (d / "kept.py").write_text("K = 1\n")
    (d / "noise.log").write_text("chatter\n")

    change = await _capture(d, base)
    assert "kept.py" in change.diff and "noise.log" not in change.diff


@pytest.mark.asyncio
async def test_an_ignored_only_dirty_tree_reads_as_no_change_and_writes_no_commit(
    tmp_path,
):
    d, base = _repo(tmp_path)
    (d / "noise.log").write_text("chatter\n")

    change = await _capture(d, base)
    assert change.status == NO_CHANGE
    assert change.head_sha == base and change.materialized is False


# ---- the outcomes that are not "a change" ---------------------------------


@pytest.mark.asyncio
async def test_an_untouched_workspace_reads_as_no_change_not_as_a_failure(tmp_path):
    """FR-006: an empty artifact is an explicit outcome, distinguishable from a
    change AND from a failure to determine one."""
    d, base = _repo(tmp_path)
    change = await _capture(d, base)
    assert change.status == NO_CHANGE
    assert change.diff == "" and change.head_sha == base


@pytest.mark.asyncio
async def test_a_workspace_that_is_not_a_repository_is_reported_not_silently_empty(
    tmp_path,
):
    """FR-007's sibling: nothing can be PUBLISHED from a non-repository either
    (delivery fails loudly on the same condition), so there is no judged-vs-
    shipped divergence — but the outcome still names itself instead of arriving
    as an empty change."""
    d = tmp_path / "plain"
    d.mkdir()
    change = await _capture(d, "")
    assert change.status == NO_REPO
    assert "not a git repository" in change.reason


@pytest.mark.asyncio
async def test_a_git_failure_is_a_failure_to_determine_the_change_not_an_empty_one(
    tmp_path, monkeypatch,
):
    """FR-007. An empty result is no longer safely equivalent to "no change", so
    a hiccup must stay distinguishable from a genuine no-op."""
    d, base = _repo(tmp_path)
    (d / "x.py").write_text("X = 1\n")

    async def _broken(host_dir, base="", head=""):
        return None  # git could not answer

    monkeypatch.setattr(queue_settle, "_git_diff", _broken)
    change = await _capture(d, base)
    assert change.status == ERROR and change.is_error
    assert "could not diff" in change.reason


@pytest.mark.asyncio
async def test_a_crash_while_materializing_is_reported_not_swallowed(
    tmp_path, monkeypatch,
):
    d, base = _repo(tmp_path)

    async def _boom(*a, **kw):
        raise OSError("git vanished")

    monkeypatch.setattr(queue_settle, "_materialize_worktree", _boom)
    change = await _capture(d, base)
    assert change.status == ERROR and "git vanished" in change.reason


def test_a_commit_that_cannot_be_written_is_an_error_not_an_empty_change(tmp_path):
    """The sync core on its own: a repository with no committable identity and a
    broken index must report ERROR, never "ok, nothing changed"."""
    d, base = _repo(tmp_path)
    (d / "x.py").write_text("X = 1\n")
    # make .git/objects unwritable so `git add` cannot store the blob
    objects = d / ".git" / "objects"
    objects.chmod(0o500)
    try:
        out = materialize_worktree_sync(str(d), base, task_id="t", message="m")
    finally:
        objects.chmod(0o755)
    assert out["status"] == ERROR and out["reason"]


# ---- authorship — machine-commit subject (spec 017) ------------------------


def test_materialization_message_is_self_describing_not_prompt_derived():
    """Spec 017 criterion 4: the commit devclaw writes when the agent authored
    none must say it is a machine snapshot, never repeat the dispatch prompt.
    The subject matches MACHINE_COMMIT_SUBJECT exactly so the PR title and the
    commit subject are always the same string."""
    msg = materialization_message("task-abc")
    subject = msg.splitlines()[0]
    assert subject == MACHINE_COMMIT_SUBJECT
    assert "machine" in subject.lower()
    assert "task-abc" in msg
    assert "Agent authored no commit" in msg


# ---- the reference pair ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_change_is_expressible_as_a_range_between_two_recorded_points(
    tmp_path,
):
    """FR-004: the pre-run reference already existed; the post-run counterpart is
    recorded beside it, so the change IS a range rather than a rendering."""
    d, base = _repo(tmp_path)
    (d / "f.py").write_text("F = 1\n")

    change = await _capture(d, base)
    assert change.base_sha == base
    assert change.head_sha and change.head_sha != base
    ranged = _git(d, "diff", change.base_sha, change.head_sha).stdout
    assert ranged == change.diff
