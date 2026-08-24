"""Doctor — read-only instance + per-project diagnostics (spec 016).

Pure reads: nothing here dispatches, mutates, or wakes the goal loop, and no
cognition call is made on any path.
"""

from __future__ import annotations

import json
from typing import Optional

from fastmcp.exceptions import ToolError

from ...doctor import run_doctor
from .._state import goals, mcp, registry, store


@mcp.tool
async def doctor(project_id: Optional[str] = None) -> str:
    """Run the devclaw doctor: mechanical, read-only diagnostics over the
    deployed instance (migration markers, legacy row shapes, OAuth credential
    expiry, skills bundle, run-window integrity, usage pause) and each
    registered project (workspace preflight, stale goal links). Codifies the
    post-redeploy checklist as named checks.

    Zero LLM calls, zero writes — every non-ok finding names the existing
    recovery verb that fixes it (doctor never executes remedies). Pass
    ``project_id`` to scope the per-project section to one project; unknown
    ids are rejected. ``healthy: true`` still lists every check as ``ok`` —
    affirmative health, never empty output."""
    if project_id is not None and registry.get(project_id) is None:
        raise ToolError(f"unknown project '{project_id}' — see list_projects")
    report = run_doctor(store, goals.goal_store, registry, project_id=project_id)
    return json.dumps(report.to_dict(), indent=2)
