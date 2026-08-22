"""Project surfaces — the control plane's repos, their status and their overrides.

``GET /projects.json`` (the list the console home renders), ``GET
/projects/{id}.json`` (one project with its goals + loose tasks), and the
per-project engine overrides (``config.json`` read / ``config`` write).
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...project_registry import _IMAGE_REF_RE as _OVR_IMAGE_RE
from ...project_registry import project_rollup
from .._state import goals, mcp, registry, store
from ._common import _task_row
from ._projections import (
    _TERMINAL_PHASES,
    _active_goal_count,
    _goal_row,
    _last_activity_ms,
    _project_event_row,
    _safe_parse,
)

#: per-project override fields the console may edit, with their validators.
_OVR_BOOL = ("automerge", "autodeploy", "review_gate", "verify_done")
_OVR_STR = {"merge_strategy": ("squash", "merge", "rebase"),
            "browser_gate_mode": ("flexible", "strict")}
#: free-form string overrides — validated by shape, not enum. sandbox_image is
#: a docker image ref (ADR 0005's escape hatch); the shared grammar (defined
#: at the registry write choke point, which also enforces it as the backstop)
#: blocks flag-shaped/whitespace junk here with a friendly 400.
_OVR_FREE_STR = ("sandbox_image",)


def _project_overrides(p) -> dict:
    return {
        "automerge": p.automerge,
        "autodeploy": p.autodeploy,
        "review_gate": p.review_gate,
        "verify_done": p.verify_done,
        "merge_strategy": p.merge_strategy,
        "browser_gate_mode": p.browser_gate_mode,
        "sandbox_image": p.sandbox_image,
    }


@mcp.custom_route("/projects/{project_id}/config.json", methods=["GET"])
async def project_config_get(request: Request) -> Response:
    """A project's editable overrides. `null` = inherit the devclaw-wide default;
    a value = pinned for this repo. Resolution is live (registry read per call)."""
    pid = request.path_params["project_id"]
    p = registry.get(pid)
    if p is None:
        return JSONResponse({"error": "not_found", "id": pid}, status_code=404)
    return JSONResponse({"overrides": _project_overrides(p)})


@mcp.custom_route("/projects/{project_id}/config", methods=["POST"])
async def project_config_set(request: Request) -> Response:
    """Update a project's overrides. Body `{field: value|null}` — only listed
    fields change (`null` clears back to the default). Unknown fields or bad
    values are rejected 400; secrets/infra env are NOT reachable here by design."""
    pid = request.path_params["project_id"]
    if registry.get(pid) is None:
        return JSONResponse({"error": "not_found", "id": pid}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict) or not body:
        return JSONResponse({"error": "empty_patch"}, status_code=400)
    patch: dict = {}
    for k, v in body.items():
        if k in _OVR_BOOL:
            if v is not None and not isinstance(v, bool):
                return JSONResponse({"error": "bad_value", "field": k, "hint": "bool|null"}, status_code=400)
            patch[k] = v
        elif k in _OVR_STR:
            if v is not None and v not in _OVR_STR[k]:
                return JSONResponse({"error": "bad_value", "field": k, "hint": f"one of {_OVR_STR[k]}|null"}, status_code=400)
            patch[k] = v
        elif k in _OVR_FREE_STR:
            if v is not None and (not isinstance(v, str) or not _OVR_IMAGE_RE.fullmatch(v)):
                return JSONResponse({"error": "bad_value", "field": k, "hint": "docker image ref|null"}, status_code=400)
            patch[k] = v
        else:
            return JSONResponse({"error": "unknown_field", "field": k}, status_code=400)
    registry.update(pid, **patch)
    return JSONResponse({"overrides": _project_overrides(registry.get(pid))})


@mcp.custom_route("/projects/{project_id}.json", methods=["GET"])
async def project_json(request: Request) -> Response:
    """Project Detail feed — header (name, repo, preview) + active/archived goal
    rows. Same phase/direction source as get_goal so any drift on the goal side
    reflects here without extra plumbing."""
    project_id = request.path_params["project_id"]
    p = registry.get(project_id)
    if p is None:
        return JSONResponse({"error": "not_found", "id": project_id}, status_code=404)
    # Discover this project's goals by project_id match — same join rule as
    # project_rollup (#524 P3, re-keyed off the old workspace-path match).
    # `goal_ids` on the Project row is advisory only and can go stale (see
    # project_registry_link_stale memory + docstring).
    matching_ids: list[str] = [
        g["id"] for g in goals.list_goals() if g.get("project_id") == p.id
    ]
    active: list[dict] = []
    archived: list[dict] = []
    for gid in matching_ids:
        row = _goal_row(gid)
        (archived if row["phase"] in _TERMINAL_PHASES else active).append(row)
    active.sort(key=lambda r: r.get("lastUpdateMs") or 0, reverse=True)
    archived.sort(key=lambda r: r.get("lastUpdateMs") or 0, reverse=True)
    # Recent standalone tasks in this project's workspace — the "loose" ones
    # not owned by any goal (dispatch_task calls). Tasks owned by a goal show
    # up inside that goal's Dispatched Tasks section, not here, so users don't
    # see double-counts. See ~/memory/projects/devclaw/plan.md "The noun model".
    loose_tasks: list[dict] = []
    if p.workspace_dir:
        for t in store.list_tasks(
            workspace_dir=p.workspace_dir,
            parent_goal_id_is_null=True,
            limit=25,
        ):
            loose_tasks.append(_task_row(t))
    # Warn-first one-goal-per-project (2026-07-04): if >1 active goal is
    # joined to this project, surface a banner. Under the standing rule a
    # project pursues one goal at a time — cancel + refile instead of stacking.
    warnings: list[dict] = []
    if len(active) > 1:
        warnings.append(
            {
                "code": "multiple_active_goals",
                "message": (
                    f"This project has {len(active)} active goals. Under the "
                    "one-goal-per-project rule a project pursues one goal at "
                    "a time — cancel the extras or refile."
                ),
                "goalIds": [row["id"] for row in active],
            }
        )
    return JSONResponse(
        {
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "repoUrl": p.repo_url,
            "previewUrl": p.preview_url,
            "active": active,
            "archived": archived,
            "tasks": loose_tasks,
            "warnings": warnings,
        }
    )


@mcp.custom_route("/projects.json", methods=["GET"])
async def projects_json(_request: Request) -> Response:
    """Projects Home feed: name, status, active goal count, last activity.

    Same source of truth as the `/projects` HTML route — project_rollup — so
    the two views can't drift. Shape is documented in
    `console/src/api.ts` (ProjectRow)."""
    out: list[dict] = []
    all_goals = goals.list_goals()
    for p in registry.list():
        rollup = project_rollup(p, all_goals)
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "status": p.status,
                "activeGoals": _active_goal_count(rollup["goals"]),
                "lastActivityMs": _last_activity_ms(rollup["goals"]),
                "repoUrl": p.repo_url or None,
                "previewUrl": p.preview_url or None,
            }
        )
    return JSONResponse(out)
