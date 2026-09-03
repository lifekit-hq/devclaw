"""Merge-on-close (spec 025 US1): the ONE seam where devclaw merges a PR.

The #641 deletion of auto-merge stands everywhere else — a goal's cumulative
PR stays open for the goal's entire life (#486), and nothing on the settle
path may merge. This module fires at exactly one moment: the done-gate has
confirmed ``achieved`` and the goal is about to close. At that point the
gates are green, the goal is complete, and nothing is left to accumulate —
so the close squash-merges the PR, and a close that cannot merge does not
happen (the goal parks loudly instead; FR-002).

Mechanical only: two ``gh`` subprocesses, no cognition, no store writes —
the caller (``tick_donegate``) owns state. Same never-raises ``_run_gh``
conventions as ``mergeability.py`` (which stays read-only per its tombstone).
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from . import mergeability as _mergeability


class MergeOutcome(enum.Enum):
    #: squash-merge performed by this call
    MERGED = "merged"
    #: the PR was already merged (an operator merged from a phone; FR-004)
    ALREADY_MERGED = "already_merged"
    #: no PR exists for the goal branch — a no-change/review-only goal;
    #: nothing to merge, the close proceeds
    NO_PR = "no_pr"
    #: GitHub refuses the merge as CONFLICTING — routes to the one bounded
    #: resolution increment (FR-017)
    CONFLICT = "conflict"
    #: the PR was closed without merging — closing the goal as achieved would
    #: silently discard its work; hard failure (FR-004)
    CLOSED_UNMERGED = "closed_unmerged"
    #: anything else (forge/network/branch-protection error) — hard failure
    ERROR = "error"


#: outcomes on which the close proceeds
SUCCESS_OUTCOMES = frozenset(
    {MergeOutcome.MERGED, MergeOutcome.ALREADY_MERGED, MergeOutcome.NO_PR}
)


@dataclass(frozen=True)
class MergeResult:
    outcome: MergeOutcome
    #: the PR url, when one was found (park messages and the resume marker)
    pr_url: str = ""
    #: the merge commit sha, best-effort (may be "" even on success)
    merged_sha: str = ""
    detail: str = ""


async def _run_gh(*argv: str, cwd: "str | None" = None) -> tuple[int, str]:
    """Best-effort subprocess: a spawn failure or a timeout returns
    ``(-1, msg)`` and never raises into the tick (mergeability.py's
    :func:`run_bounded`)."""
    return await _mergeability.run_bounded(*argv, cwd=cwd)


#: stderr fragments gh emits when the merge is blocked by a conflict. Kept
#: deliberately narrow: an unrecognized refusal parks as ERROR (loud) rather
#: than burning the heal budget on a non-conflict.
_CONFLICT_MARKERS = ("not mergeable", "merge conflict", "conflicting")


async def attempt_merge(workspace_dir: str, branch: str) -> MergeResult:
    """Locate the open PR for ``branch`` and squash-merge it.

    Runs ``gh`` with the workspace as cwd so the repo resolves from the
    clone's origin — the same host-credential path delivery uses. One
    bounded internal retry on a pure subprocess failure; every other
    non-success is returned for the caller to classify.
    """
    rc, out = await _run_gh(
        "gh", "pr", "view", branch,
        "--json", "url,state,mergeCommit", cwd=workspace_dir,
    )
    if rc != 0:
        if "no pull requests found" in out.lower() or "could not find" in out.lower():
            return MergeResult(MergeOutcome.NO_PR, detail=out[:200])
        # one retry for a transient forge/network hiccup (FR-002)
        rc, out = await _run_gh(
            "gh", "pr", "view", branch,
            "--json", "url,state,mergeCommit", cwd=workspace_dir,
        )
        if rc != 0:
            if "no pull requests found" in out.lower() or "could not find" in out.lower():
                return MergeResult(MergeOutcome.NO_PR, detail=out[:200])
            return MergeResult(MergeOutcome.ERROR, detail=f"gh pr view failed: {out[:300]}")
    try:
        info = json.loads(out)
    except ValueError:
        return MergeResult(MergeOutcome.ERROR, detail=f"unparseable gh pr view output: {out[:200]}")
    pr_url = str(info.get("url") or "")
    state = str(info.get("state") or "").upper()
    merge_commit = (info.get("mergeCommit") or {}) or {}
    if state == "MERGED":
        return MergeResult(
            MergeOutcome.ALREADY_MERGED, pr_url=pr_url,
            merged_sha=str(merge_commit.get("oid") or ""), detail="already merged",
        )
    if state == "CLOSED":
        return MergeResult(
            MergeOutcome.CLOSED_UNMERGED, pr_url=pr_url,
            detail="PR closed without merge — the goal's work would be discarded",
        )
    rc, out = await _run_gh("gh", "pr", "merge", pr_url, "--squash", cwd=workspace_dir)
    if rc != 0:
        low = out.lower()
        if any(m in low for m in _CONFLICT_MARKERS):
            return MergeResult(MergeOutcome.CONFLICT, pr_url=pr_url, detail=out[:300])
        return MergeResult(MergeOutcome.ERROR, pr_url=pr_url, detail=f"gh pr merge failed: {out[:300]}")
    rc, out = await _run_gh(
        "gh", "pr", "view", pr_url, "--json", "mergeCommit", cwd=workspace_dir,
    )
    sha = ""
    if rc == 0:
        try:
            sha = str(((json.loads(out).get("mergeCommit") or {}) or {}).get("oid") or "")
        except ValueError:
            sha = ""
    return MergeResult(MergeOutcome.MERGED, pr_url=pr_url, merged_sha=sha, detail="squash-merged")


async def sync_workspace_to_default(workspace_dir: str) -> None:
    """Best-effort post-merge sync (FR-005 belt-and-braces): fetch and
    fast-forward the workspace's default branch so the next queued goal's
    prepare starts from the merged head. Never raises; the dispatch-time
    ``prepare_ws`` remains the guarantee."""
    rc, out = await _run_gh("git", "fetch", "origin", cwd=workspace_dir)
    if rc != 0:
        return
    rc, head = await _run_gh(
        "git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short", cwd=workspace_dir,
    )
    default = head.rsplit("/", 1)[-1] if rc == 0 and head else "main"
    await _run_gh("git", "checkout", default, cwd=workspace_dir)
    await _run_gh("git", "reset", "--hard", f"origin/{default}", cwd=workspace_dir)
