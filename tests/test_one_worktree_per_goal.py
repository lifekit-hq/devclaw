"""One goal, one worktree (#952): a goal is leased a fresh treehouse worktree
only when treehouse SAYS it holds none. An unreadable pool status (a failed
call, a timeout, garbage output) is not "no lease" — leasing on it would strand
the goal's first worktree, branch work and all, as a lease nothing returns.
The lease raises instead, and the tick settles the row INTERRUPTED (unleased)
and tries again next tick.

Driven through the ``_treehouse_sync`` seam the conftest guard names — no real
treehouse.
"""

import json

import pytest

from devclaw.engine import workspace as ws


def _pool(*trees: dict) -> str:
    return json.dumps(list(trees))


@pytest.fixture()
def treehouse(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls: list[tuple[str, ...]] = []

    def use(status: "tuple[int, str]"):
        def fake(project_workspace, *args):
            calls.append(args)
            if args[0] == "status":
                return status
            if args[0] == "get":
                return 0, json.dumps({"path": "/pool/fresh"})
            return 0, ""
        monkeypatch.setattr(ws, "_treehouse_sync", fake)
        return calls
    return str(tmp_path), use


@pytest.mark.parametrize("status", [
    (1, "pool locked by another treehouse process"),
    (127, "treehouse not runnable: timed out after 300 seconds"),
    (0, "update available — run treehouse upgrade"),
    (0, json.dumps({"error": "unexpected shape"})),
], ids=["failed", "timeout", "not-json", "not-a-pool"])
async def test_an_unreadable_pool_never_leases_a_second_worktree(treehouse, status):
    workspace, use = treehouse
    calls = use(status)
    with pytest.raises(ws.WorkspaceError):
        await ws.lease_goal_checkout(workspace, None, "g1")
    assert [c[0] for c in calls] == ["status"]
    assert ws.goal_checkout_dir(workspace, "g1") is None


async def test_the_goals_lease_is_reused_and_only_a_goal_with_none_gets_one(treehouse):
    workspace, use = treehouse
    calls = use((0, _pool(
        {"path": "/pool/other", "status": "leased", "lease_holder": "devclaw:g2"},
        {"path": "/pool/mine", "status": "leased", "lease_holder": "devclaw:g1"},
        {"path": "/pool/idle", "status": "available"},
    )))
    assert await ws.lease_goal_checkout(workspace, None, "g1") == "/pool/mine"
    assert [c[0] for c in calls] == ["status"]

    calls.clear()
    assert await ws.lease_goal_checkout(workspace, None, "g3") == "/pool/fresh"
    assert [c[0] for c in calls] == ["status", "get"]
    assert calls[1][calls[1].index("--lease-holder") + 1] == "devclaw:g3"
