# Plan: Spec 028 — Stale worker inputs

## US1 surface: direct dispatch workspace reset

Files touched:
- `devclaw/queue/settle.py` — `SettleMixin._execute`: add `elif` after the existing
  `if (base_branch or target_branch)` block. Condition:
  `not (base_branch or target_branch) and not (row and row.parent_goal_id) and not (row and row.pause_count > 0)`.
  Call `prepare_workspace(workspace_dir, branch=None)`. On `WorkspaceError` or
  generic `Exception`, log to stderr and continue (best-effort, not fail-closed —
  goal-path tasks have no origin on local workspaces).
- `tests/test_delivery.py` — two test changes:
  1. Add `parent_goal_id="goal-fixture"` to the existing
     `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape`
     submit call so the test correctly models goal-path behavior (which IS exempt).
  2. Add `test_direct_dispatch_without_target_branch_resets_to_origin_head` using
     the `_clone_with_origin` fixture. Assert: prep is called with `branch=None`,
     the workspace ends on the default branch, and the task completes.

Constraint: `prepare_workspace` is already a module-level global in settle.py
(imported at line ~53). Tests that monkeypatch `devclaw.queue.settle.prepare_workspace`
continue to work.

## US2 surface: issue staleness grading

Files touched (next session):
- `devclaw/prompts/intake-readiness.md` — add a staleness check section to the
  prompt: "does the described condition still hold in the repository?" Report as
  a `stale` boolean in the output JSON.
- `devclaw/intake_readiness.py` — add `stale: bool` field to `ReadinessVerdict`;
  parse it from the model output in `validate()`; surface in the not-ready
  missing list when stale.
- `tests/` — named regression test.

## Judgment calls

- Best-effort (not fail-closed) for direct dispatch prep matches the spirit of
  the tiny spec `direct-dispatch-workspace-reset.md` and avoids breaking local
  test setups.
- US1 ships standalone as PR 1; US2 ships as PR 2.
- The tiny spec at `specs/tiny/direct-dispatch-workspace-reset.md` covers the
  same code change and is updated as done in the US1 PR.
