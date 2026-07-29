"""Best-effort git subprocess helpers used by the task queue's gate/pause paths.

Three blocking ``subprocess.run`` bodies, split out of :mod:`devclaw.task_queue`
verbatim. They are BLOCKING on purpose — ``asyncio.create_subprocess_exec`` hangs
under pytest's per-test event loops (child-watcher pitfall) and is overkill for a
sub-second git call. Every one is strictly best-effort: a hiccup (not a repo, git
missing, timeout) degrades gracefully instead of blocking a run.

The thin ``async`` wrappers that offload these to a thread live in
:mod:`devclaw.task_queue` (``_git_diff`` / ``_git_head`` / ``_wip_snapshot``) so
their module-global lookup of these names stays patchable there.
"""

from __future__ import annotations

import os
import subprocess

from .git_identity import git_identity_env

#: Generic manifest / entrypoint files worth grounding the reviewer on — one per
#: common ecosystem (Python, Node, .NET, Go, Rust, Java) plus the repo's own
#: convention docs and a verify entrypoint. Deliberately NOT tuned to any single
#: target repo: the point is to tell the reviewer WHICH stack it is looking at,
#: and the ``tracked_top_level`` listing covers anything these miss.
_REPO_CONTEXT_PROBES = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "scripts/verify.sh",
    "Makefile",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "global.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
)


def _run_git_context(host_dir: str, *args: str) -> str:
    """One best-effort ``git -C host_dir <args>`` → trimmed stdout, or a short
    ``<...>`` marker on any failure. Never raises — same best-effort contract as
    the diff/head helpers, so a git hiccup while gathering review context can
    never fail a task."""
    try:
        p = subprocess.run(
            ["git", "-C", host_dir, *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<git error: {exc.__class__.__name__}>"
    if p.returncode != 0:
        return f"<git exited {p.returncode}>"
    return (p.stdout or "").strip()


def _review_repo_context_sync(host_dir: str) -> str:
    """A small, grounded snapshot of the task's ACTUAL repo for the host-side
    review gate. The reviewer sees only the ticket + diff, never the sandbox
    filesystem; for a tiny diff (e.g. a lone CI YAML) it otherwise has no way to
    know which repo it is judging and can substitute the control-plane repo that
    host-side ``claude`` was launched from. These facts — remote, branch, head,
    key-file presence, tracked top-level layout — anchor it to the real
    workspace.

    Strictly best-effort like the rest of this module: any failure degrades to a
    partial or omitted line and it NEVER raises, so it cannot fail a task closed
    through the reviewer's fail-closed path."""
    if not os.path.isdir(host_dir):
        return f"workspace_dir: {host_dir} (not present)"
    facts = [
        f"workspace_dir: {host_dir}",
        f"git_remote_origin: {_run_git_context(host_dir, 'remote', 'get-url', 'origin')}",
        f"git_branch: {_run_git_context(host_dir, 'branch', '--show-current')}",
        f"git_head: {_run_git_context(host_dir, 'log', '-1', '--oneline')}",
    ]
    for rel in _REPO_CONTEXT_PROBES:
        try:
            path = os.path.join(host_dir, rel)
            kind = "dir" if os.path.isdir(path) else "file" if os.path.isfile(path) else "missing"
        except (OSError, TypeError):
            kind = "unknown"
        facts.append(f"{rel}: {kind}")
    files = _run_git_context(host_dir, "ls-files")
    if files and not files.startswith("<"):
        top = sorted({line.split("/", 1)[0] for line in files.splitlines() if line.strip()})
        if top:
            facts.append("tracked_top_level: " + ", ".join(top[:40]))
    return "\n".join(facts)


def _git_diff_sync(host_dir: str, base: str = "") -> str:
    """The agent's change as a unified diff. With ``base`` (the pre-run HEAD),
    diff the working tree against that ref — which captures work the agent
    already COMMITTED as well as staged/unstaged edits. The commit coda asks
    the agent to commit, and in goal-branch mode those commits land directly on
    ``goal/<id>``: judging only the uncommitted tree made a fully-committed
    change look like a no-op to the integrity + review gates (live-found
    2026-07-11: three bench tasks in a row got "requested changes" on a diff of
    trend-file noise while the real work sat committed on the goal branch).
    Without ``base`` — or when the ref is unresolvable — fall back to the
    legacy uncommitted-only view.

    Blocking subprocess.run (with a timeout) — NOT asyncio.create_subprocess_exec,
    which hangs under pytest's per-test event loops (child-watcher pitfall) and is
    overkill for a sub-second git call. Best-effort: '' on any failure (not a repo,
    git missing, timeout) so a hiccup never blocks a legitimately-good task."""
    if base:
        try:
            p = subprocess.run(
                ["git", "-C", host_dir, "diff", base],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if p.returncode == 0:
            return p.stdout
        # unresolvable ref — fall through to the uncommitted-only view
    out = ""
    for args in (["diff"], ["diff", "--cached"]):
        try:
            p = subprocess.run(
                ["git", "-C", host_dir, *args],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if p.returncode == 0:
            out += p.stdout
    return out


def _git_commit_exists_sync(host_dir: str, sha: str) -> bool:
    """True when ``sha`` resolves to a commit in ``host_dir`` — the guard for
    re-using a persisted gate baseline on a pause-resume re-run. Best-effort
    like the rest of this module: any hiccup reads as "does not resolve" so
    the caller degrades to a fresh capture instead of raising."""
    if not sha:
        return False
    try:
        p = subprocess.run(
            ["git", "-C", host_dir, "rev-parse", "--verify", "--quiet",
             f"{sha}^{{commit}}"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _git_head_sync(host_dir: str) -> str:
    """Current HEAD sha, or '' when unavailable — the pre-run baseline for
    :func:`_git_diff_sync`. Best-effort for the same reason: a baseline hiccup
    must degrade to the legacy diff view, never block the run."""
    try:
        p = subprocess.run(
            ["git", "-C", host_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _git_reset_clean_sync(host_dir: str, sha: str) -> bool:
    """Hard-reset the worktree to ``sha`` and drop every untracked file — the
    clean per-item base a retry attempt re-runs from (#1 retry isolation).
    Best-effort like the other helpers here: returns True only when BOTH the
    reset and the clean succeed, False on any hiccup (empty ``sha``, an
    OSError, a non-zero git) so the caller can proceed on the drifted tree
    with a logged miss rather than wedging the retry. Local-only by
    construction — the queue pushes a goal branch only AFTER the attempt loop
    and only on a gate pass, so rewinding local commits here never touches
    origin."""
    if not sha:
        return False
    try:
        reset = subprocess.run(
            ["git", "-C", host_dir, "reset", "--hard", sha],
            capture_output=True, text=True, timeout=30,
        )
        clean = subprocess.run(
            ["git", "-C", host_dir, "clean", "-fdx"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return reset.returncode == 0 and clean.returncode == 0


def _ls_remote_ok_sync(repo_url: str) -> bool:
    """Is ``repo_url`` reachable right now? One blocking ``git ls-remote``
    (refs listing only — no clone, no checkout), the cheapest end-to-end
    reachability probe git offers: it exercises DNS + auth + repo existence,
    the exact failure surface ``prepare_workspace``'s clone/fetch dies on.
    Used by the goal tick's prep-block auto-heal recheck.

    Blocking subprocess.run with a short timeout — same child-watcher
    rationale as :func:`_git_diff_sync`; the tick offloads it to a thread.
    ``GIT_TERMINAL_PROMPT=0`` forces a credential prompt (private/nonexistent
    https repo on a headless host) to fail fast instead of hanging to the
    timeout. Strictly best-effort: NEVER raises — any failure (timeout, git
    missing, non-zero exit) reads as "still unreachable" and the caller's
    backoff continues."""
    try:
        p = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _base_branch_error_sync(host_dir: str, base_branch: str) -> str | None:
    """Validate a caller-chosen PR base at the dispatch surface
    (v1-helper-resurface PR-2): fetch origin, then require
    ``origin/<base_branch>`` to resolve. Returns None when the base is good,
    else the actionable failure message the task fails with.

    Deliberately NOT best-effort, unlike the diff/baseline helpers above: a
    bogus base that slipped past dispatch surfaces downstream as diff-range /
    PR-base skew (``_default_base_ref`` silently falls through to the remote
    default), which is exactly the quiet degrade this check exists to prevent.
    ``GIT_TERMINAL_PROMPT=0`` for the same headless-host fail-fast rationale
    as :func:`_ls_remote_ok_sync`."""
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", host_dir, *args],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    try:
        fetch = run("fetch", "origin", "--prune")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"base_branch '{base_branch}' could not be validated: git fetch failed ({exc})"
    if fetch.returncode != 0:
        tail = (fetch.stdout + fetch.stderr).strip()[-300:]
        return (
            f"base_branch '{base_branch}' could not be validated: git fetch "
            f"origin failed in the workspace: {tail}"
        )
    try:
        probe = run("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base_branch}")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"base_branch '{base_branch}' could not be validated: {exc}"
    if probe.returncode != 0:
        return (
            f"base_branch '{base_branch}' does not resolve on the workspace's "
            f"origin (no origin/{base_branch} after fetch) — the PR base and "
            "diff range would silently skew to the default branch. Push the "
            "base branch to origin first, or dispatch with an existing base."
        )
    return None


def branch_staleness_sync(host_dir: str, base_branch: str) -> dict | None:
    """How far HEAD has drifted from ``origin/<base_branch>``.

    Returns ``{"commits_behind": int, "commits_ahead": int}`` when both
    rev-list counts succeed, or ``None`` on any failure (not a repo, git
    missing, timeout, non-integer output, non-zero exit). Strictly
    best-effort — callers degrade to "proceed unchanged" on ``None``.

    Assumes the caller already fetched ``origin`` and that
    ``origin/<base_branch>`` resolves; this is NOT the fetch primitive
    (use :func:`_base_branch_error_sync` for that validation step).

    Blocking subprocess.run — same child-watcher rationale as
    :func:`_git_diff_sync`. Never raises."""
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", host_dir, *args],
            capture_output=True, text=True, timeout=30,
        )

    try:
        behind = run("rev-list", "--count", f"HEAD..origin/{base_branch}")
        ahead = run("rev-list", "--count", f"origin/{base_branch}..HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    if behind.returncode != 0 or ahead.returncode != 0:
        return None
    try:
        return {
            "commits_behind": int(behind.stdout.strip()),
            "commits_ahead": int(ahead.stdout.strip()),
        }
    except ValueError:
        return None


def _wip_snapshot_sync(host_dir: str, task_id: str) -> str:
    """Commit the interrupted attempt's uncommitted work as a WIP snapshot
    before a usage-limit requeue. The workspace survives the requeue untouched
    (nothing re-preps between requeue and re-run), but a dirty tree is fragile:
    anything that later resets/cleans the workspace (prepare_workspace's
    ``reset --hard`` + ``clean -fdx`` on the goal's next dispatch, should this
    task ultimately fail) wipes it. A commit makes the partial work durable.

    Blocking subprocess.run with timeouts — same child-watcher rationale as
    :func:`_git_diff_sync`. Strictly best-effort: returns ``"committed"`` when
    a snapshot commit was made, else a short reason (not a repo, git missing,
    timeout, nothing to commit, git error) — the caller logs it and proceeds
    with the requeue either way; a snapshot hiccup must never block the pause
    path."""
    def run(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", host_dir, *args],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, **env_extra} if env_extra else None,
        )

    try:
        status = run("status", "--porcelain")
        if status.returncode != 0:
            return "not a git repo"
        if not status.stdout.strip():
            return "clean tree — nothing to snapshot"
        add = run("add", "-A")
        if add.returncode != 0:
            return f"git add failed: {(add.stderr or '').strip()[:120]}"
        # Identity via GIT_* env (not -c): env beats every config level, so an
        # ambient/leaked identity can't author devclaw's snapshot commit.
        commit = run(
            "commit", "-m",
            f"wip(devclaw): interrupted by usage limit (task {task_id[:8]})",
            env_extra=git_identity_env(),
        )
        if commit.returncode != 0:
            return f"git commit failed: {(commit.stderr or '').strip()[:120]}"
        return "committed"
    except (OSError, subprocess.SubprocessError) as err:
        return f"{err.__class__.__name__}: {err}"
