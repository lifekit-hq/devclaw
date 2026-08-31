# Tasks: Stale worker inputs (spec 028)

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)

## US1 — Direct dispatch workspace reset (P1)

- [x] Add `elif` in `devclaw/queue/settle.py::_execute` for direct-dispatch workspace reset
- [x] Update `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape` to add `parent_goal_id`
- [x] Add `test_direct_dispatch_without_target_branch_resets_to_origin_head` in `tests/test_delivery.py`
- [x] Mark tiny spec `specs/tiny/direct-dispatch-workspace-reset.md` tasks as done
- [x] Full suite + `ruff check .` + `mypy` green; open PR 1

## US2 — Issue staleness grading (P2)

- [x] Add staleness check to `devclaw/prompts/intake-readiness.md`
- [x] Add `stale: bool` field to `ReadinessVerdict` in `devclaw/intake_readiness.py`
- [x] Parse staleness result in `validate()` and surface in missing list
- [x] Add `test_stale_issue_graded_not_ready_when_condition_already_resolved` in `tests/`
- [x] Full suite + `ruff check .` + `mypy` green; open PR 2

## Post-merge steering (increment 2)

- [x] Narrow `except Exception` in `settle.py::_execute` to `except WorkspaceError` for the best-effort/no-remote case; unexpected exceptions now set `prep_failure` and surface as `mark_failed`
- [x] Add `test_direct_dispatch_workspace_reset_unexpected_exception_marks_failed` and `test_direct_dispatch_workspace_reset_workspace_error_is_best_effort` in `tests/test_delivery.py`
- [x] Fix CLAUDE.md: replace full backtick-path references to `.claude/commands/ship.md`, `.claude/hooks/`, `.claude/skills/` with relative form so `test_harness_docs_map` passes in the sandbox environment
- [x] Full suite + `ruff check .` + `mypy` green

## Post-merge steering (increment 3)

- [x] Distinguish no-remote `WorkspaceError` (best-effort) from origin-configured fetch failure (mark_failed): add `_proc_run("git remote get-url origin")` pre-check in `settle.py::_execute`; `_has_origin=True` + `WorkspaceError` → `prep_failure` (spec 028 steering 2026-08-31)
- [x] Add `test_direct_dispatch_workspace_reset_origin_fetch_failure_marks_failed` in `tests/test_delivery.py`
- [x] Fix `test_review_gate_grounded_in_actual_workspace_repo` to monkeypatch `prepare_workspace` (the test's origin URL is unreachable in CI; it was relying on the now-removed best-effort-for-all-WorkspaceErrors behavior)
- [x] Full suite (1258 passed) + `ruff check .` + `mypy` green
