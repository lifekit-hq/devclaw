"""The ONE answer to "what did the agent change?" (spec 013, #630).

Two components used to compute that independently and disagree. Delivery staged
everything and committed, so it could not miss a file. The gates diffed the
working tree against the pre-run ref, which shows only content the agent chose
to *record* — and what made the agent record its work was a sentence in a worker
skill file. When the agent complied both views agreed; when it did not, the gates
judged a strict subset of what shipped, and a change made entirely of new
unrecorded files reached every gate as an EMPTY span and passed them trivially
(live, 2026-08-22: delivery shipped 4 files / +179, the gates judged 1 / +32).

This module removes the second computation. **Materialization** happens once, on
the host, the moment the agent's run ends: stage everything, write it into a
commit, and hand back a :class:`ChangeSet` naming both ends of the range. Gates,
the change-size projection, the advisory checks and delivery all read that one
object, so two consumers cannot disagree about what the change is — the invariant
stops being a docstring and becomes structural.

Deliberately NOT best-effort, unlike :mod:`devclaw.task_git`'s helpers: an empty
result is no longer safely equivalent to "no change", so a git failure is
reported as :data:`ERROR` (the caller's gate fails it CLOSED, #186) instead of
degrading to ``""``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from .git_identity import git_identity_env

#: the agent produced a real span — gates judge it, delivery publishes it
CHANGE = "change"
#: the agent changed nothing. A first-class outcome: the task settles
#: successfully, publishes nothing, and is reported upstream as no progress.
NO_CHANGE = "no_change"
#: the workspace is not a git repository. Distinct from ERROR on purpose: the
#: judged-vs-shipped divergence this module exists to close cannot occur without
#: a repository — delivery already fails loudly there ("workspace is not a git
#: repository" is NOT a benign error), so nothing can ship unjudged. Still
#: reported out loud; never presented as an empty change.
NO_REPO = "no_repo"
#: the workspace IS a repository and git could not answer. Fails CLOSED.
ERROR = "error"


@dataclass(frozen=True)
class ChangeSet:
    """Everything the agent changed, as one object.

    ``base_sha`` is the task's pinned pre-run reference; ``head_sha`` is its
    post-run counterpart, so the change is expressible as a range between two
    points. ``diff`` is that range rendered once — the single value every
    consumer reads.
    """

    status: str
    base_sha: str = ""
    head_sha: str = ""
    diff: str = ""
    #: why, for :data:`ERROR` / :data:`NO_REPO`
    reason: str = ""
    #: the AGENT itself wrote the commit at ``head_sha``. Measured before
    #: staging, so it answers "did the worker write a commit message this run",
    #: not delivery's old ``ahead > 0`` proxy (true in goal-branch mode for
    #: prior increments this worker never touched).
    agent_authored: bool = False
    #: devclaw created or amended a commit to capture unrecorded work
    materialized: bool = False

    @property
    def is_error(self) -> bool:
        return self.status == ERROR

    @property
    def is_change(self) -> bool:
        return self.status == CHANGE

    @property
    def is_no_change(self) -> bool:
        return self.status == NO_CHANGE


def _git(host_dir: str, *args: str, env_extra: "dict[str, str] | None" = None):
    return subprocess.run(
        ["git", "-C", host_dir, *args],
        capture_output=True, text=True, timeout=60,
        env=({**os.environ, **env_extra} if env_extra else None),
    )


def materialize_worktree_sync(
    host_dir: str, base: str, *, task_id: str, message: str
) -> dict:
    """Capture everything the agent left in ``host_dir`` as a git commit.

    Mechanical by construction — ``git add -A`` cannot miss a file, and no
    instruction to the agent is load-bearing for its completeness (FR-002).

    Returns ``{"status", "head", "agent_authored", "materialized", "reason"}``
    where status is ``ok`` / ``no_repo`` / ``error``.

    * A CLEAN tree makes no commit at all, so a worker that recorded all of its
      own work ends the run with exactly the history it has today — no extra
      commit, no altered message (FR-008).
    * A dirty tree is folded into the worker's OWN commit when it made one this
      run and that commit is unpushed (``commit --amend --no-edit``) — the same
      rule delivery has applied for the stray-lockfile case, applied to the whole
      class instead of one instance. Otherwise devclaw writes the commit, with
      the same message delivery composes today.
    * ``git add -A`` honours ``.gitignore``, so ignored files are excluded from
      the judged span exactly as they are from what ships.
    """
    out: dict = {"status": ERROR, "head": "", "agent_authored": False,
                 "materialized": False, "reason": ""}
    try:
        probe = _git(host_dir, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError) as exc:
        out["status"] = NO_REPO
        out["reason"] = f"git is not usable in {host_dir}: {exc.__class__.__name__}: {exc}"
        return out
    if probe.returncode != 0:
        out["status"] = NO_REPO
        out["reason"] = f"{host_dir} is not a git repository"
        return out

    try:
        if base:
            ahead = _git(host_dir, "rev-list", "--count", f"{base}..HEAD")
            if ahead.returncode == 0 and ahead.stdout.strip().isdigit():
                out["agent_authored"] = int(ahead.stdout.strip()) > 0

        status = _git(host_dir, "status", "--porcelain")
        if status.returncode != 0:
            out["reason"] = f"git status failed: {(status.stderr or '').strip()[:200]}"
            return out

        if status.stdout.strip():
            add = _git(host_dir, "add", "-A")
            if add.returncode != 0:
                out["reason"] = f"git add -A failed: {(add.stderr or '').strip()[:200]}"
                return out
            staged = _git(host_dir, "diff", "--cached", "--quiet")
            # rc 1 => something is staged; rc 0 => nothing (the dirt was all
            # ignored/untracked-and-ignored, which does not ship either).
            if staged.returncode != 0:
                pushed = _git(host_dir, "branch", "-r", "--contains", "HEAD")
                head_is_pushed = pushed.returncode == 0 and bool(pushed.stdout.strip())
                if out["agent_authored"] and not head_is_pushed:
                    commit = _git(host_dir, "commit", "--amend", "--no-edit",
                                  env_extra=git_identity_env())
                else:
                    commit = _git(host_dir, "commit", "-m", message,
                                  env_extra=git_identity_env())
                if commit.returncode != 0:
                    out["reason"] = (
                        f"git commit failed: "
                        f"{((commit.stderr or '') + (commit.stdout or '')).strip()[:200]}"
                    )
                    return out
                out["materialized"] = True

        head = _git(host_dir, "rev-parse", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            out["reason"] = f"git rev-parse HEAD failed: {(head.stderr or '').strip()[:200]}"
            return out
        out["head"] = head.stdout.strip()
        out["status"] = "ok"
        return out
    except (OSError, subprocess.SubprocessError) as exc:
        out["reason"] = f"{exc.__class__.__name__}: {exc}"
        return out


def materialization_message(title: str, task_id: str) -> str:
    """The commit message devclaw writes when the worker recorded nothing —
    byte-identical to the one delivery composed at its own commit site, so a
    worker that never commits still gets the same PR title and branch slug."""
    return f"{title}\n\nDelivered by devclaw (task {task_id})."
