# Phase 1 — Contract: dispatch tool signatures (P1 hard cutover)

The caller-facing MCP contract change. `workspace_dir` is **removed** from every
dispatch/goal entrypoint and replaced by a required `project_id`. This is the
breaking change the OpenClaw waiter prompt must migrate to in lockstep.

## Before → After

### `dispatch_task` (`server/tools.py:32`)

```diff
 async def dispatch_task(
     kind: Literal["implement_feature", "fix_bug", "review_repository"],
-    workspace_dir: str,
+    project_id: str,
     goal: str,
     notify_url: Optional[str] = None,
     verify_cmd: Optional[str] = None,
     open_pr: bool = False,
     base_branch: Optional[str] = None,
     target_branch: Optional[str] = None,
 ) -> str:
```
Behavior: resolve `project_id` → `workspace_dir` via `registry`; unknown →
`ToolError`; preflight the resolved workspace (exists + `.git`) before
`queue.submit`; then `queue.submit(workspace_dir=<resolved>, …)` unchanged.

### `implement_feature` / `fix_bug` / `review_repository` (`:96 / :117 / :140`)

Same swap: `workspace_dir: str` → `project_id: str`. They forward through
`dispatch_task`, so they only pass `project_id` along (resolution happens once in
`dispatch_task`).

### `onboard` (`:213`)

`workspace_dir: str` → `project_id: str`; resolve before its own
`queue.submit` (`:250`).

### `create_goal` (`:724`)

```diff
 async def create_goal(
     goal_id: str,
     objective: str,
-    workspace_dir: str,
+    project_id: str,
     done_when: ...,
-    repo_url: Optional[str] = None,
     ...
 ) -> str:
```
`workspace_dir` AND `repo_url` are both resolved from the row (the row is now the
one place they live). Forward to `goals.create_goal(workspace_dir=<resolved>,
repo_url=<resolved>, …)`.

### `start_program` (`:271`, deprecated alias)

`workspace_dir: str` → `project_id: str`; resolve before
`goals.create_goal` (`:296`).

## Rejection contract (synchronous, zero-work)

| Condition | Response |
|---|---|
| `project_id` empty/missing | `ToolError("<tool> requires project_id and …")` — mirrors current `:80-81` guard shape. |
| `project_id` not in registry | `ToolError("unknown project_id: '<id>' — register it first")` — mirrors `project_status` (`:1327-1329`). No task/goal row, no engine call. |
| resolved workspace missing / not a git repo | Loud admission reject with the path + cause + remedy. Direct path → `ToolError` before `submit`; goal path → `mechanical:prep` block via `_block_on_prep_failure`. |

## Explicitly NOT changed

- `queue.submit` (`task_queue.py:785`) and `goals.create_goal` signatures keep
  their `workspace_dir` (+ `repo_url`) params — resolution feeds them the
  resolved value. Services stay `project_id`-unaware (FR-007).
- The dry-run tools (`_dry_goal`, `:557`) keep `workspace_dir="/dev/null"`
  internally — they take no caller `project_id` (harness-internal, clarify Q1).
- Registry CRUD tools (`register_project`/`update_project`/`project_status`/…)
  already take `project_id`; they gain write-time path validation, not a
  signature change.

## Waiter-prompt contract (out of repo — lockstep release step)

The OpenClaw waiter (Denys-owned, `dsdevq/lifekit-stack` GitOps) must be updated
to emit `project_id` instead of `workspace_dir` for all dispatch/goal calls, in
the same landing window as this PR's deploy. A devclaw deploy that lands these
signatures without the waiter update breaks dispatch on the VPS (FR-008a).
