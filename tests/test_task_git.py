"""Unit tests for devclaw.task_git.branch_staleness_sync.

The sync helpers in task_git are tested by patching the module-global
``subprocess`` name so that tests exercise each code path without a real
git repo or network.  Every test verifies one of the four distinct
branches: happy-path count parsing, non-zero exit, OSError/SubprocessError
(including TimeoutExpired), and ValueError on non-integer stdout.
"""
from __future__ import annotations

import subprocess


from devclaw import task_git


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeSubprocess:
    """Drop-in for the subprocess module in task_git's namespace.

    Preserves the real exception classes so that ``except (OSError,
    subprocess.SubprocessError)`` clauses in the patched module resolve
    correctly.
    """
    SubprocessError = subprocess.SubprocessError
    TimeoutExpired = subprocess.TimeoutExpired
    CompletedProcess = subprocess.CompletedProcess

    def __init__(self, responses: list[tuple[int, str]] | None = None, raises: BaseException | None = None) -> None:
        self._responses = iter(responses or [])
        self._raises = raises

    def run(self, *args, **kwargs) -> _FakeProc:
        if self._raises is not None:
            raise self._raises
        rc, stdout = next(self._responses)
        return _FakeProc(rc, stdout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_branch_staleness_sync_happy_path(monkeypatch):
    """Both rev-list calls succeed → dict with correct counts."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess([(0, "3\n"), (0, "1\n")]),
    )
    result = task_git.branch_staleness_sync("/repo", "main")
    assert result == {"commits_behind": 3, "commits_ahead": 1}


def test_branch_staleness_sync_zero_drift(monkeypatch):
    """Both counts are 0 (branch is in sync) → dict with zeros, not None."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess([(0, "0\n"), (0, "0\n")]),
    )
    result = task_git.branch_staleness_sync("/repo", "main")
    assert result == {"commits_behind": 0, "commits_ahead": 0}


def test_branch_staleness_sync_nonzero_returncode_returns_none(monkeypatch):
    """A non-zero exit from either rev-list call → None."""
    # First call (behind) fails
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess([(128, ""), (0, "1\n")]),
    )
    assert task_git.branch_staleness_sync("/repo", "main") is None


def test_branch_staleness_sync_ahead_nonzero_returns_none(monkeypatch):
    """Non-zero from the second (ahead) rev-list call → None."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess([(0, "3\n"), (128, "")]),
    )
    assert task_git.branch_staleness_sync("/repo", "main") is None


def test_branch_staleness_sync_oserror_returns_none(monkeypatch):
    """OSError (git binary missing, not a repo) → None, never raises."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess(raises=OSError("git not found")),
    )
    assert task_git.branch_staleness_sync("/repo", "main") is None


def test_branch_staleness_sync_timeout_returns_none(monkeypatch):
    """TimeoutExpired (a SubprocessError subclass) → None, never raises."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess(raises=subprocess.TimeoutExpired("git", 30)),
    )
    assert task_git.branch_staleness_sync("/repo", "main") is None


def test_branch_staleness_sync_nonnumeric_stdout_returns_none(monkeypatch):
    """Non-integer stdout from rev-list (corrupt output) → None."""
    monkeypatch.setattr(
        task_git, "subprocess",
        _FakeSubprocess([(0, "not-a-number\n"), (0, "1\n")]),
    )
    assert task_git.branch_staleness_sync("/repo", "main") is None


# ---------------------------------------------------------------------------
# One REAL-git test (no stub): the fresh-goal-branch fact the #439 livelock
# hinged on — a brand-new goal branch sitting at origin's default tip really
# does read 0 ahead / 0 behind, so the dispatch predicate must proceed on it
# (skip only on 0-ahead AND >= threshold-behind), never on 0-ahead alone.
# ---------------------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def test_branch_staleness_sync_fresh_goal_branch_reads_zero_zero(tmp_path):
    """A goal branch created at origin/main's tip (the first-item / re-seeded
    case) reads 0 ahead / 0 behind against the REAL probe — proving the stubbed
    dispatch tests match production and the livelock predicate is sound."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)],
                   check=True, capture_output=True, text=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)],
                   check=True, capture_output=True, text=True)
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "f.txt").write_text("base\n")
    _git(seed, "add", "f.txt")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")

    # A worker workspace: clone origin, branch goal/g AT the default tip (exactly
    # what workspace.prepare does for a fresh first item — 0 ahead / 0 behind).
    ws = tmp_path / "ws"
    subprocess.run(["git", "clone", "-q", str(origin), str(ws)],
                   check=True, capture_output=True, text=True)
    _git(ws, "checkout", "-q", "-b", "goal/g")

    result = task_git.branch_staleness_sync(str(ws), "main")
    assert result == {"commits_behind": 0, "commits_ahead": 0}
