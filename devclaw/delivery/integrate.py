"""Lane integration — folding one fan-out increment onto the shared goal branch.

Spec 010 US3 (FR-102). A fan-out lane runs in its OWN workspace, because two
agents cannot share one checkout; its work therefore has to be brought back to
the goal branch before it can ship. That is this module: commit the lane's tree,
then merge it into the shared workspace.

Two decisions worth stating, because both remove a whole class of trouble:

* **Integration is local.** The shared workspace fetches straight from the lane
  DIRECTORY (git speaks filesystem remotes), so lanes never contend on a remote
  branch, never need a credential of their own, and nothing is ever force-pushed.
  Delivery — the one push, the one cumulative PR — happens afterwards from the
  shared workspace, exactly as it does for a sequential increment.
* **A conflict fails the lane, it does not get resolved.** Lanes are admitted
  only with pairwise-disjoint declared scopes, and the declared-scope gate has
  already verified each lane stayed inside its own. So a conflict here means an
  assumption broke, and the honest response is to fail that lane loudly and
  leave the shared branch exactly as it was (``git merge --abort``) — never to
  guess at a resolution, and never to leave a half-merged tree behind for the
  next lane to inherit.

The caller supplies the serialization (:class:`devclaw.loom.merge_queue.MergeQueue`);
nothing here assumes it is alone, but nothing here defends against a second
writer either — the queue is what makes that true. Zero LLM.
"""

from __future__ import annotations

from ..git_identity import git_identity_env
from . import _run

#: Marker in the lane's own commit, so a human reading the goal branch can tell
#: which increment a change came from without consulting devclaw.
LANE_TRAILER = "Devclaw-Lane"


async def commit_lane(workspace_dir: str, *, label: str, task_id: str) -> "str | None":
    """Commit whatever the lane's agent left in its tree. Returns an error string
    or ``None``.

    A clean tree is success, not failure: the agent may already have committed
    (the brief asks it to), in which case there is simply nothing more to record
    and its commits are integrated as they are."""
    rc, out = await _run("git", "status", "--porcelain", cwd=workspace_dir)
    if rc != 0:
        return f"lane workspace is not a usable git repository: {out[:400]}"
    if not out.strip():
        return None  # already committed by the agent — nothing to add
    rc, out = await _run("git", "add", "-A", cwd=workspace_dir)
    if rc != 0:
        return f"could not stage the lane's changes: {out[:400]}"
    message = f"feat(lane): {label}\n\n{LANE_TRAILER}: {task_id}\n"
    rc, out = await _run(
        "git", "commit", "-m", message, cwd=workspace_dir, env_extra=git_identity_env()
    )
    if rc != 0:
        return f"could not commit the lane's changes: {out[:400]}"
    return None


async def integrate_lane(
    *, lane_dir: str, into_dir: str, label: str, task_id: str
) -> "str | None":
    """Merge one lane's commits into the shared workspace. Error string or ``None``.

    Called under the merge queue, so ``into_dir`` is this lane's alone for the
    duration. A conflict aborts the merge and reports — the shared branch is left
    byte-identical to how it arrived."""
    rc, out = await _run("git", "fetch", lane_dir, "HEAD", cwd=into_dir)
    if rc != 0:
        return f"could not fetch lane '{task_id}' from {lane_dir}: {out[:400]}"
    rc, out = await _run(
        "git", "merge", "--no-edit", "-m",
        f"merge(lane): {label}\n\n{LANE_TRAILER}: {task_id}\n", "FETCH_HEAD",
        cwd=into_dir, env_extra=git_identity_env(),
    )
    if rc == 0:
        return None
    detail = out[:600]
    # Leave nothing half-merged for the next lane in the queue.
    await _run("git", "merge", "--abort", cwd=into_dir)
    return (
        f"lane '{task_id}' could not be integrated onto the goal branch: {detail}. "
        f"Lanes are admitted only with disjoint declared file scopes, so a "
        f"conflict means the plan's independence claim was wrong — the merge was "
        f"aborted and the goal branch is unchanged."
    )
