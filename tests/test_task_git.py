"""Unit tests for devclaw.task_git.branch_staleness_sync.

The sync helpers in task_git are tested by patching the module-global
``subprocess`` name so that tests exercise each code path without a real
git repo or network.  Every test verifies one of the four distinct
branches: happy-path count parsing, non-zero exit, OSError/SubprocessError
(including TimeoutExpired), and ValueError on non-integer stdout.
"""
from __future__ import annotations

import subprocess

import pytest

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
