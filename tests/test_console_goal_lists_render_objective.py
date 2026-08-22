"""Every goal-list surface renders the goal's `objective` (its intent) as the
primary label, not the bare machine `id`. The payload carries `objective`
(locked in test_console_goal_row_objective.py); this pins that all four list
render sites actually USE it, so no surface silently regresses back to a wall
of UUIDs. Source-assertion style, matching test_console_project_count_usage.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1] / "console" / "src"

# (relative path, description) for each surface that renders a goal row.
_LIST_SITES = [
    ("pages/Overview.tsx", "Overview cards + recently-active strip"),
    ("pages/Goals.tsx", "global Goals table"),
    ("pages/ProjectDetail.tsx", "project's active/archived goal rows"),
    ("components/Fleet.tsx", "Node fleet drill-down goal lines"),
]


@pytest.mark.parametrize("rel,desc", _LIST_SITES)
def test_goal_list_site_renders_objective_not_bare_id(rel: str, desc: str):
    src = (_CONSOLE / rel).read_text()

    # The intent must be rendered — `g.objective` (with a `|| g.id` fallback for
    # an unresolved goal). Its presence is the durable contract.
    assert "g.objective" in src, (
        f"{rel} ({desc}) must render g.objective as the primary label so the "
        "list reads as intent, not a wall of machine ids"
    )

    # And it must NOT have reverted to labeling a goal by the bare id alone —
    # the debug-dump smell this change removed. `{g.id}` may still appear as the
    # demoted mono handle, but only alongside the objective.
    assert "{g.objective || g.id}" in src, (
        f"{rel} ({desc}) must use the `objective || id` fallback so a missing "
        "objective degrades to the id rather than showing blank"
    )
