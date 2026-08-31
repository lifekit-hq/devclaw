# Tasks: Stale worker inputs (spec 027)

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)

## US1 — Direct dispatch workspace reset (P1)

- [x] Add `elif` in `devclaw/queue/settle.py::_execute` for direct-dispatch workspace reset
- [x] Update `test_task_without_branch_params_never_preps_and_keeps_legacy_delivery_shape` to add `parent_goal_id`
- [x] Add `test_direct_dispatch_without_target_branch_resets_to_origin_head` in `tests/test_delivery.py`
- [x] Mark tiny spec `specs/tiny/direct-dispatch-workspace-reset.md` tasks as done
- [x] Full suite + `ruff check .` + `mypy` green; open PR 1

## US2 — Issue staleness grading (P2)

- [ ] Add staleness check to `devclaw/prompts/intake-readiness.md`
- [ ] Add `stale: bool` field to `ReadinessVerdict` in `devclaw/intake_readiness.py`
- [ ] Parse staleness result in `validate()` and surface in missing list
- [ ] Add `test_stale_issue_graded_not_ready_when_condition_already_resolved` in `tests/`
- [ ] Full suite + `ruff check .` + `mypy` green; open PR 2
