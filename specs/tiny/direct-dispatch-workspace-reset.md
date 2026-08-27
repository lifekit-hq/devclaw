# TinySpec: direct dispatch without target_branch resets to default branch head

**Issue**: #491
**Branch**: goal/devclaw-auth-ping-path-2026-08-25
**Date**: 2026-08-27
**Status**: implementing
**Complexity**: small

## What

A direct dispatch (`dispatch_task` with no `target_branch`) currently runs the
engine in whatever state the workspace was last left in — often a stale feature
branch from a prior task. The engine should start on a fresh checkout of the
repository's current default branch head, exactly as the goal layer does before
each action.

## Context

The goal layer calls `prepare_workspace(workspace_dir, branch=None)` before
submitting each task via `_dispatch_action` (`goal/tick_dispatch.py` line 109).
The direct dispatch path (`queue/settle.py` `_execute`) only preps the workspace
when `target_branch` or `base_branch` is set — the unpinned path has NO workspace
reset, landing the engine on whatever branch a prior task left.

| File | Role |
|------|------|
| `devclaw/queue/settle.py` | `SettleMixin._execute` — branch prep section (~line 673) |
| `tests/test_delivery.py` | Existing test to update + new regression test |

## Requirements

1. When `target_branch` and `base_branch` are both None AND `parent_goal_id` is
   None (direct dispatch) AND it is not a pause-resume, `_execute` calls
   `prepare_workspace(workspace_dir, branch=None)` to reset to `origin/<default>`.
2. Goal-path tasks (`parent_goal_id` set) skip this prep — the goal tick already
   ran `prepare_workspace` before dispatch; re-prepping would reset the goal branch.
3. Pause-resume tasks skip this prep (workspace survives the requeue untouched).
4. Prep failure is best-effort + loud: a `WorkspaceError` or other exception is
   logged to stderr and the task continues (the workspace runs in whatever state
   it is). In production every registered workspace has an origin remote so
   `prepare_workspace` always succeeds; local-only workspaces (tests, local
   checkouts) gracefully degrade rather than blocking the dispatch.
5. The existing test
   `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape`
   is updated to add `parent_goal_id` (simulating the goal path it always
   intended to test) so it correctly asserts no prep for goal-dispatched tasks.
6. Named regression test:
   `test_direct_dispatch_without_target_branch_resets_to_origin_head`
   verifies that the engine starts on the default branch after workspace reset.

## Plan

1. `devclaw/queue/settle.py` `_execute`: add `elif` after the existing
   `if (base_branch or target_branch)` block. Condition:
   `not (base_branch or target_branch) and not (row and row.parent_goal_id) and not (row and row.pause_count > 0)`.
   Call `prepare_workspace(workspace_dir)`. On `WorkspaceError` or generic
   `Exception`, set `prep_failure` (same fail-loud pattern as `_prep_branch_target`).
2. `tests/test_delivery.py`:
   - Add `parent_goal_id="goal-fixture"` to
     `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape`'s
     submit call; update docstring to match.
   - Add `test_direct_dispatch_without_target_branch_resets_to_origin_head` using
     `_clone_with_origin` fixture so the workspace has a real remote. Assert the
     workspace lands on the default branch after dispatch, and the task completes.

## Tasks

- [x] Write tinyspec
- [ ] Implement `elif` in `_execute` (`settle.py`)
- [ ] Update `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape`
- [ ] Add `test_direct_dispatch_without_target_branch_resets_to_origin_head`
- [ ] Full suite + `ruff check .` + `mypy` green

## Done When

- `_execute` resets unpinned direct dispatch tasks to `origin/<default>` before
  the engine runs
- Goal-path tasks (with `parent_goal_id`) are byte-unaffected
- Named regression test passes
- Existing test updated to correctly model goal-path behavior
