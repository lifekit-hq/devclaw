"""Blocking git helpers the settle path offloads to a thread. Every one is
best-effort: a hiccup degrades to ``None`` / ``""`` / a reason string, never
an exception — the caller decides what a missing answer means."""

from __future__ import annotations

import os
import subprocess

from .git_identity import git_identity_env


def _git_commit_exists_sync(host_dir: str, sha: str) -> bool:
    if not sha:
        return False
    try:
        p = subprocess.run(
            ["git", "-C", host_dir, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _git_head_sync(host_dir: str) -> str:
    try:
        p = subprocess.run(["git", "-C", host_dir, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _git_tracked_changes_sync(host_dir: str) -> "str | None":
    """Changes to tracked files (``git status --porcelain``, untracked files
    left out), ``""`` on a clean tree, None when git cannot tell."""
    try:
        p = subprocess.run(["git", "-C", host_dir, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _wip_commit_sync(host_dir: str, message: str) -> str:
    """Commit any uncommitted changes as a WIP commit. Returns ``"committed"``,
    ``"clean tree"``, or a short reason. Never raises."""
    def run(*args: str, env_extra: "dict[str, str] | None" = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", host_dir, *args], capture_output=True, text=True, timeout=30,
            env={**os.environ, **env_extra} if env_extra else None,
        )

    try:
        status = run("status", "--porcelain")
        if status.returncode != 0:
            return "not a git repo"
        if not status.stdout.strip():
            return "clean tree"
        add = run("add", "-A")
        if add.returncode != 0:
            return f"git add failed: {(add.stderr or '').strip()[:120]}"
        commit = run("commit", "-m", message, env_extra=git_identity_env())
        if commit.returncode != 0:
            return f"git commit failed: {(commit.stderr or '').strip()[:120]}"
        return "committed"
    except (OSError, subprocess.SubprocessError) as err:
        return f"{err.__class__.__name__}: {err}"
