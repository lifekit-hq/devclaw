"""Repo lifecycle + durable deploy hosting.

``create_repo``/``delete_repo`` (the managed-repo ledger guards live here) and
the Tailscale deploy verbs. See ``devclaw/delivery/``.
"""

from __future__ import annotations

import json

from fastmcp.exceptions import ToolError

from ...delivery import deploy as _deploy
from ...delivery import repo as _repo
from .._state import mcp, registry


# ===== build a project from scratch ==========================================


@mcp.tool
async def create_repo(
    name: str,
    private: bool = False,
    description: str = "",
) -> str:
    """Create a fresh GitHub repo under the configured account so a from-scratch
    goal has somewhere to live. Returns {created, existed, repo, clone_url}. The
    repo is seeded with a README (initial commit + a 'main' default branch) so it
    can be cloned and PR'd against immediately. Idempotent: if the name already
    exists it returns that repo instead of failing. Feed the returned clone_url
    into create_goal(repo_url=...). Auth is gh's own login (repo write access).
    Public by default — Actions on private repos never start under the
    account's billing lock; pass private=true only for sensitive content."""
    if not name:
        raise ToolError("create_repo requires a name")
    try:
        result = await _repo.create_repo(name, private=private, description=description)
    except _repo.RepoError as err:
        raise ToolError(str(err))
    if result.get("created"):
        # Provenance: only repos devclaw itself stood up enter the managed-repo
        # ledger — an `existed` hit is somebody else's repo and must never
        # become deletable through delete_repo.
        registry.record_managed_repo(result["repo"])
    return json.dumps(result, indent=2)


@mcp.tool
async def delete_repo(name: str, confirm: str = "") -> str:
    """Permanently DELETE a GitHub repo that devclaw itself created — the teardown
    counterpart of create_repo, for retiring scratch/bench repos without operator
    gh gymnastics. IRREVERSIBLE, and guarded four ways: the repo must be in
    devclaw's managed-repo ledger (recorded by create_repo — a pre-existing,
    human-owned repo can NEVER be deleted here, whatever is passed); `confirm`
    must echo the repo's exact 'owner/name' slug (call once without it and the
    error tells you the exact string); the repo must not be referenced by any
    registered project — delete_project first; and the gh token must carry the
    delete_repo scope (grant once via `gh auth refresh -h github.com -s
    delete_repo`). An unknown repo raises, so a typo never silently no-ops. This
    is an operator verb on the MCP surface only — nothing in the autonomous loop
    calls it."""
    if not name:
        raise ToolError("delete_repo requires a name")
    # Ownership is the first gate: devclaw only deletes what devclaw stood up.
    # Check BOTH the input-derived slug and the confirm slug — gh follows
    # renames, and the ledger holds the slug as it was at creation time.
    managed_candidates = {_repo.full_slug(name)}
    if "/" in confirm:
        managed_candidates.add(confirm)
    if not any(registry.is_managed_repo(c) for c in managed_candidates):
        raise ToolError(
            f"repo '{name}' is not in devclaw's managed-repo ledger — devclaw "
            "only deletes repos it created itself via create_repo. A pre-existing "
            "or human-owned repo must be deleted by hand with gh."
        )
    # Second gate: a repo still referenced by a registered project can't be
    # deleted out from under it. Matches on BOTH slugs for the same rename
    # reason (deletion only proceeds when `confirm` echoes the canonical slug).
    candidates = {_repo.slug_repo_name(name).lower()}
    if "/" in confirm:
        candidates.add(confirm.rsplit("/", 1)[-1].lower())
    referenced = [
        p.id
        for p in registry.list()
        if any(
            (p.repo_url or "").rstrip("/").removesuffix(".git").lower().endswith(f"/{c}")
            for c in candidates
        )
    ]
    if referenced:
        raise ToolError(
            f"repo '{name}' is still referenced by registered project(s) "
            f"{referenced} — delete_project (or update_project) first, "
            "then delete the repo"
        )
    try:
        out = await _repo.delete_repo(name, confirm=confirm)
    except _repo.RepoError as err:
        raise ToolError(str(err))
    # The repo is gone — retire every ledger alias for it (creation-time slug
    # and canonical slug can differ after a rename).
    for slug in managed_candidates | {out["repo"]}:
        registry.forget_managed_repo(slug)
    return json.dumps(out, indent=2)


# ===== durable deploy hosting ================================================
# Long-lived, reboot-surviving container at a STABLE per-slug URL over Tailscale.
# Auto-fires when a goal reaches `achieved` (see goal_tick). See devclaw/deploy.py.


@mcp.tool
async def deploy_project(workspace_dir: str, slug: str) -> str:
    """Deploy a project's BUILT app as a DURABLE host on the VPS and return its stable
    Tailscale URL — so the owner is HANDED a running product to open, not a diff to
    read. Survives reboots (--restart unless-stopped), lives at a fixed per-slug
    port so the URL never changes across redeploys, and is reachable over Tailscale
    (https, auto-TLS, never public). Idempotent: redeploying the same slug replaces
    the container at the same URL. workspace_dir = the goal's checkout; slug = a
    short stable name."""
    if not workspace_dir or not slug:
        raise ToolError("deploy_project requires workspace_dir and slug")
    try:
        return json.dumps(await _deploy.deploy_project(workspace_dir, slug), indent=2)
    except _deploy.DeployError as err:
        raise ToolError(str(err))


@mcp.tool
async def deploy_status(slug: str) -> str:
    """Status of a durable deploy: whether it exists, is running, is answering
    (ready), its stable Tailscale URL, and the one-time serve command."""
    if not slug:
        raise ToolError("deploy_status requires slug")
    return json.dumps(await _deploy.deploy_status(slug), indent=2)


@mcp.tool
async def stop_deploy(slug: str) -> str:
    """Stop and remove a durable deploy, tear down its Tailscale serve, and free its
    VPS resources."""
    if not slug:
        raise ToolError("stop_deploy requires slug")
    return json.dumps(await _deploy.stop_deploy(slug), indent=2)


@mcp.tool
async def list_deploys() -> str:
    """List all durable deploys (running + stopped) with their status."""
    return json.dumps(await _deploy.list_deploys(), indent=2)
