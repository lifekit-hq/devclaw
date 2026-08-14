# Phase 1 — Data Model: project reference key (P1)

No schema change in P1. This documents the entities resolution reads and the new
in-memory value it produces.

## Project (existing registry row — unchanged schema)

`project_registry.py:94-126` / table `:218-235`. Fields relevant to dispatch
resolution:

| Field | Type | Role in resolution |
|---|---|---|
| `id` | TEXT PRIMARY KEY | **The reference key.** `registry.get(id)` (`:315`). |
| `workspace_dir` | TEXT (nullable) | Resolved target; fed to `queue.submit`/`goals.create_goal`. |
| `repo_url` | TEXT (nullable) | Resolved for goal dispatch (clone source; P2 auto-prep). |
| `automerge, autodeploy, review_gate, verify_done` | INTEGER 0/1 (nullable) | Override knobs — NOT threaded through dispatch in P1; resolved at settle/merge via the existing workspace-path join. |
| `merge_strategy, browser_gate_mode, sandbox_image` | TEXT (nullable) | Same — path-join resolved. |
| `status` | TEXT | `active`/`paused`/`archived` — dispatch SHOULD reject a non-active project (see FR/validation note). |

**Validation rules (new, P1 — write-time):**
- `workspace_dir` on `create`/`update` is normalized via `_normalize_workspace`
  (`:512`) and stored canonical (today: stored raw — the bug).
- The path is treated as **container-side** (serving-process frame); a path that
  cannot be a valid container-side path is rejected with a reason (mirror
  `_validate_sandbox_image`, `:75-82`).
- Unknown `id` on the dispatch path → resolution miss → `ToolError`.

**State/transition note**: dispatch against a `paused`/`archived` project is a
candidate loud-reject (deferred to `/speckit-tasks` — not a spec requirement,
but the natural place to enforce it now that dispatch reads the row).

## ResolvedDispatch (new — transient, not persisted)

The value `registry.resolve_dispatch(project_id)` returns (or the tuple resolved
inline at the seam). Purely in-memory; consumed at the tool seam and discarded.

| Field | Source | Consumed by |
|---|---|---|
| `workspace_dir` | `Project.workspace_dir` (normalized) | `queue.submit(workspace_dir=…)` / `goals.create_goal(workspace_dir=…)` |
| `repo_url` | `Project.repo_url` | `goals.create_goal(repo_url=…)` (goal seams only) |

**Not included**: the override knobs. They are intentionally left to the
existing path-keyed joins (`merge.py:66,81`; `task_queue.py:1945,1958,1967`)
which resolve them from the SAME `workspace_dir` that resolution populated onto
the row (FR-007). Threading knobs through `ResolvedDispatch` is P3, not P1.

## Task / Goal row (existing — unchanged shape)

The resolved `workspace_dir` is written onto the task row (`state_store` via
`queue.submit`, `task_queue.py:823-841`) and the goal row (`GoalStore` via
`goals.create_goal`) exactly as a caller-supplied `workspace_dir` is today. This
is what keeps every downstream path-keyed join (`find_by_workspace_dir`,
`project_rollup`, override resolution) working with zero change — resolution
*populates* the same field the old raw param used to fill.

## Entity relationships

```
project_id ──(registry.get)──▶ Project row ──▶ workspace_dir ──┐
                                     │                          ├─▶ task/goal row (persisted)
                                     └──▶ repo_url ─────────────┘        │
                                                                         ▼
                              existing path-keyed joins (unchanged): find_by_workspace_dir,
                              resolve_override (knobs), project_rollup, resolve_automerge
```

The reference key resolves once at dispatch; everything downstream stays
workspace-path-keyed in P1. P3 would migrate those joins to `project_id`.
