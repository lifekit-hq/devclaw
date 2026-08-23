"""Workspace lifecycle — give the engine a clean checkout per goal action.

Folded in from goalclaw. Now that the goal layer lives INSIDE devclaw, devclaw
owns the goal↔repo↔workspace lifecycle end to end (this is the seam-relocation
the 2026-06-06 brainstorm wanted — it falls out for free once the two services
are one process). Before each code action we make the workspace a **pristine
checkout of the repo's default branch at latest origin** — clone if missing,
else fetch + hard-reset + clean. That keeps a multi-item goal's actions from
piling onto each other's branches, and naturally picks up whatever PRs got
merged since the last action.
"""

from __future__ import annotations

import re
from pathlib import Path
from ..procutil import run as _run


class WorkspaceError(RuntimeError):
    pass


def workspace_is_dispatchable(workspace_dir: str | None) -> str | None:
    """Dispatch-time preflight predicate (spec 003 / #520, US2): is this
    workspace a real git checkout a task can run in RIGHT NOW? Returns a
    human-actionable reason string when it is NOT dispatchable, or ``None`` when
    it is fine to proceed.

    Zero-token and zero-container by construction — a couple of filesystem stats,
    nothing more — so it stays on the cheap side of the dispatch seam and never
    trips the zero-token idle guard. Callers invoke it at ADMISSION (before a row
    is claimed / any ``docker run``): the direct path rejects with a ToolError,
    the goal path blocks-and-heals (``mechanical:prep``). This replaces the late,
    post-claim, silent failure inside the sandbox (``engine/sandcastle.py`` only
    checked existence at launch, not git-ness). Mirrors the ``.git`` predicate
    :func:`prepare_workspace` already uses below."""
    if not workspace_dir or not str(workspace_dir).strip():
        return "no workspace_dir to dispatch into"
    root = Path(workspace_dir)
    if not root.exists():
        return (
            f"workspace {workspace_dir!r} does not exist — the registry row points "
            f"at a path that isn't on disk (clone it, or fix the project's "
            f"workspace_dir / repo_url)"
        )
    if not (root / ".git").exists():
        return (
            f"workspace {workspace_dir!r} is not a git checkout (no .git) — set the "
            f"project's repo_url so devclaw can clone it, or point it at a real checkout"
        )
    return None



async def _default_branch(workspace_dir: str) -> str:
    """The remote's default branch name (e.g. 'main'). Falls back main→master."""
    rc, out = await _run(
        "git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", cwd=workspace_dir
    )
    if rc == 0 and "/" in out:
        return out.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        rc, _ = await _run("git", "rev-parse", "--verify", "--quiet", f"origin/{cand}", cwd=workspace_dir)
        if rc == 0:
            return cand
    return "main"


async def _merged_pr_head(
    workspace_dir: str, branch: str, base_branch: str
) -> str | None:
    """The head SHA of the most recently MERGED GitHub PR from ``branch``
    INTO ``base_branch``, or None when there is none / it cannot be
    determined. This is the #394 re-seed signal: git alone cannot tell that a
    squash-merge landed a branch's content (the squashed commit is
    patch-equivalent to nothing on the branch), so we ask GitHub. The
    ``--base`` filter matters: a PR merged into some OTHER branch says
    nothing about this branch's standing versus the base we would re-seed
    from. Best-effort and never raises — any failure (non-GitHub remote, no
    gh, network, unparseable output) returns None and prep behaves exactly
    as before. Module global so tests patch it here; the remote-url gate
    keeps the suite's file-remote fixtures at zero gh subprocesses."""
    rc, url = await _run("git", "remote", "get-url", "origin", cwd=workspace_dir)
    if rc != 0 or "github.com" not in url:
        return None
    rc, out = await _run(
        "gh", "pr", "list", "--head", branch, "--base", base_branch,
        "--state", "merged",
        "--limit", "1", "--json", "headRefOid", "--jq", ".[0].headRefOid",
        cwd=workspace_dir,
    )
    if rc != 0:
        return None
    sha = out.strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


async def _reseed_merged_branch(
    workspace_dir: str, branch: str, default_branch: str, merged_head: str,
) -> bool:
    """Re-seed ``branch`` after its PR merged (#394 done-when 2): a goal
    branch whose PR was squash-merged conflicts with the default branch *by
    construction* from the next delivery on — the branch still carries the
    pre-squash commits main now contains in squashed form (the closeloop-bench
    PR #8 morning: three gate-green deliveries piled onto a spent branch, none
    could land). Returns True when the workspace is ready on a re-seeded
    ``branch``; False means "no safe re-seed — fall back to today's
    reset-to-remote-tip", in which case delivery still works and the settle-
    time mergeability probe makes any conflict loud.

    Two shapes, both reconciling the REMOTE too (delivery pushes plain, so a
    local-only re-seed would break the next push):

    - **Fully landed** (remote tip == the merged PR's head): the branch is
      spent. Delete it on origin — the end state a merge with
      ``--delete-branch`` leaves — and start fresh from the default branch.
      Remote delete goes FIRST: if it is refused, keep today's behavior
      rather than leave a re-seeded local racing a stale remote.
    - **Tip moved past the merged head** (a delivery landed after the owner
      merged): rebase only the post-merge commits onto the default tip and
      force-with-lease the remote to match. Any rebase conflict or push
      refusal reverts to the remote tip — conservative, never half-done.
    """
    rc, tip = await _run("git", "rev-parse", f"origin/{branch}", cwd=workspace_dir)
    if rc != 0:
        return False
    tip = tip.strip()

    if tip == merged_head:
        rc, _ = await _run("git", "push", "origin", "--delete", branch, cwd=workspace_dir)
        if rc != 0:
            return False
        rc, out = await _run(
            "git", "checkout", "-f", "-B", branch, f"origin/{default_branch}",
            cwd=workspace_dir,
        )
        if rc != 0:
            raise WorkspaceError(
                f"re-seed of merged goal branch {branch} failed: {out[-300:]}"
            )
        return True

    # Post-merge commits exist beyond the merged head. Only rebase history we
    # understand: the merged head must be an ancestor of the remote tip.
    rc, _ = await _run(
        "git", "merge-base", "--is-ancestor", merged_head, tip, cwd=workspace_dir
    )
    if rc != 0:
        return False
    rc, _ = await _run(
        "git", "checkout", "-f", "-B", branch, f"origin/{branch}", cwd=workspace_dir
    )
    if rc != 0:
        return False
    rc, _ = await _run(
        "git",
        "-c", "user.email=devclaw@localhost",
        "-c", "user.name=devclaw",
        "rebase", "--onto", f"origin/{default_branch}", merged_head, branch,
        cwd=workspace_dir,
    )
    if rc != 0:
        await _run("git", "rebase", "--abort", cwd=workspace_dir)
        await _run(
            "git", "checkout", "-f", "-B", branch, f"origin/{branch}", cwd=workspace_dir
        )
        return False
    rc, _ = await _run(
        "git", "push", "--force-with-lease", "origin", branch, cwd=workspace_dir
    )
    if rc != 0:
        await _run(
            "git", "checkout", "-f", "-B", branch, f"origin/{branch}", cwd=workspace_dir
        )
        return False
    return True


async def prepare_workspace(
    workspace_dir: str,
    repo_url: str | None = None,
    branch: str | None = None,
    base_branch: str | None = None,
) -> str:
    """Ensure ``workspace_dir`` is a pristine checkout of either the repo's
    default branch (when ``branch`` is None) OR the named ``branch`` at its
    latest tip. Clones from ``repo_url`` if the dir isn't a repo. Returns the
    name of the branch the working tree ended up on. Raises
    :class:`WorkspaceError` on failure.

    The ``branch`` arg is the Pillar 1 / Pillar 2 hook: each per-item task in
    a checklist-mode goal passes ``branch="goal/<goal_id>"`` so subsequent
    items see the prior items' commits stacked on the goal branch instead of
    branching off origin/main and re-implementing the foundation (the
    2026-06-26 finance-sentry-mcp-v3 PR-fan-out failure). When the goal
    branch doesn't exist yet (first item) it is created from the default
    branch at latest origin; otherwise it is fetched + reset to its own
    remote tip (preserving the agent's accumulated work) — EXCEPT when that
    branch's PR has since been MERGED (#394): a squash-merged branch
    conflicts with the default branch by construction, so prep re-seeds it
    (fully-landed branch → deleted on origin + recreated fresh from the
    default tip; post-merge commits → rebased onto the default tip, remote
    force-with-lease'd to match) so delivery N+1 starts landable. See
    :func:`_reseed_merged_branch`; every non-understood shape falls back to
    the plain reset-to-remote-tip.

    ``base_branch`` (v1-helper-resurface P1, proposal O3) only matters when a
    named ``branch`` does NOT exist on origin yet: the fresh branch is created
    off ``origin/<base_branch>`` instead of the remote default, so a direct
    task pinning ``target_branch`` + a non-default ``base_branch`` starts from
    the base it will PR back to. None (every goal-path caller) ⇒ today's
    create-off-default behavior, byte-identical.

    Injected into the goal tick so unit tests pass a no-op.
    """
    if not (Path(workspace_dir) / ".git").exists():
        if not repo_url:
            raise WorkspaceError(
                "no repo to work in — this goal has no repo_url and its workspace "
                f"({workspace_dir}) isn't a git checkout. Set the goal's repo_url to "
                "the GitHub repo I should clone, or tell me to start a fresh empty "
                "repo here (I won't `git init` on my own — that's yours to confirm)."
            )
        Path(workspace_dir).parent.mkdir(parents=True, exist_ok=True)
        rc, out = await _run("git", "clone", repo_url, workspace_dir)
        if rc != 0:
            raise WorkspaceError(f"clone failed: {out[-300:]}")

    rc, out = await _run("git", "fetch", "origin", "--prune", cwd=workspace_dir)
    if rc != 0:
        raise WorkspaceError(f"fetch failed: {out[-300:]}")

    default_branch = await _default_branch(workspace_dir)

    if branch is None or branch == default_branch:
        # Unpinned / discovery / done-gate path — just reset to the default branch.
        for cmd in (
            ("git", "checkout", "-f", default_branch),
            ("git", "reset", "--hard", f"origin/{default_branch}"),
            ("git", "clean", "-fdx"),
        ):
            rc, out = await _run(*cmd, cwd=workspace_dir)
            if rc != 0:
                raise WorkspaceError(f"{' '.join(cmd)} failed: {out[-300:]}")
        return default_branch

    # Goal-branch path. Start clean (drop any untracked debris from a prior
    # task), then either fast-forward the existing branch to its remote tip
    # OR create it fresh from the default branch.
    rc, _ = await _run("git", "clean", "-fdx", cwd=workspace_dir)
    if rc != 0:
        # clean failure is rare and not load-bearing here — log via the error
        # below if a subsequent op trips on residue.
        pass

    rc_remote, _ = await _run(
        "git", "rev-parse", "--verify", "--quiet", f"origin/{branch}",
        cwd=workspace_dir,
    )
    # Both checkouts force (-f): harness writes between actions (e.g. the
    # trend detector appending to a TRACKED .devclaw/trends.md) leave
    # tracked-file modifications that `clean -fdx` can't touch, and an
    # unforced `checkout -B` refuses to overwrite them — wedging the goal on
    # its own mechanism output. Pristine means pristine: local dirt never
    # survives prep, whatever produced it.
    if rc_remote == 0:
        # #394: if this branch's PR has merged since the last prep, the branch
        # is spent (or carries a spent base) — re-seed so the next delivery
        # starts landable instead of piling onto a structurally-conflicting
        # branch. Best-effort: no merged PR / no GitHub / any doubt → the
        # plain reset below, exactly the pre-#394 behavior.
        #
        # HARD-SCOPED to devclaw's own ``goal/<id>`` namespace: the re-seed
        # deletes and force-pushes REMOTE branches, and the only branches
        # devclaw is the sole writer of are the ones GoalBranchStrategy mints
        # (``goal/{goal_id}``). The ADR 0011 direct-task path routes
        # arbitrary HUMAN-owned ``target_branch`` names through this same
        # prep — a merged-then-reused feature branch there must never be
        # deleted or history-rewritten out from under its owner.
        if branch.startswith("goal/"):
            merged_head = await _merged_pr_head(workspace_dir, branch, default_branch)
            if merged_head is not None and await _reseed_merged_branch(
                workspace_dir, branch, default_branch, merged_head
            ):
                return branch
        # Branch exists on origin — check it out and reset to its tip so we
        # have ALL prior items' commits. (A force-reset is safe because we
        # never write to this branch except via push from devclaw itself.)
        rc, out = await _run(
            "git", "checkout", "-f", "-B", branch, f"origin/{branch}",
            cwd=workspace_dir,
        )
        if rc != 0:
            raise WorkspaceError(f"checkout goal branch {branch} failed: {out[-300:]}")
    else:
        # First item of the goal — branch from origin/<default> so the goal
        # starts at the same point a single-PR rerun would. A direct task's
        # caller-chosen base (v1-helper-resurface O3) wins when provided.
        start_ref = f"origin/{base_branch or default_branch}"
        rc, out = await _run(
            "git", "checkout", "-f", "-B", branch, start_ref,
            cwd=workspace_dir,
        )
        if rc != 0:
            raise WorkspaceError(
                f"create branch {branch} from {start_ref} failed: {out[-300:]}"
            )
    return branch
