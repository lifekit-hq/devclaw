# Tasks: One Dispatch Lane (spec 022)

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

---

## US1 (P1): Issue-keyed dispatch — create-or-attach

Covers FR-001 through FR-003, FR-005 through FR-009, FR-011, FR-012.

- [x] T001 Add `goal_issue_identity` table to `GoalState._bootstrap()` in `devclaw/goal/state.py`
- [x] T002 Add `_claim_issue_identity()`, `_rearm_issue_identity()`, `_lookup_issue_identity()` to `GoalStateContentMixin` in `devclaw/goal/state_content.py`
- [x] T003 Add `claim_issue_identity()`, `rearm_issue_identity()`, `lookup_issue_identity()` wrappers to `GoalContentMixin` in `devclaw/goal/store/content.py`
- [x] T004 Add `dispatch_issue()` async method to `GoalService` in `devclaw/goal/service.py`
- [x] T005 Add `issue_ref: Optional[int] = None` to `dispatch_task`, `implement_feature`, `fix_bug` in `devclaw/server/tools/tasks.py`
- [x] T006 Add `check_goal_issue_identity_table` doctor check to `devclaw/doctor/checks_instance.py`
- [x] T007 Write named regression tests in `tests/test_issue_keyed_dispatch.py`

---

## US2 (P2): Companion dispatches ride the full goal lane

Covers FR-004 (repeal single-writer exemption + workspace prep).

- [x] T008 Repeal `_project_hold_warning` advisory-only; replace with hard block for issue-keyed dispatch in `devclaw/server/tools/tasks.py`
- [x] T009 Workspace-prep-to-default-branch-head before each run in `dispatch_issue()` in `devclaw/goal/service.py`
- [x] T010 Write named regression tests for serialization and workspace prep

---

## US3 (P3): Retire freeform-prose path + demolish program/DAG

Covers FR-010, FR-008 (read-only kinds unaffected). Demolition scope.

- [ ] T011 `dispatch_task` without `issue_ref` for mutating kinds → auto-file issue + proceed
- [ ] T012 Delete `devclaw/queue/programs.py` DAG machinery + `devclaw/goal/fanout.py`
- [ ] T013 Delete `tests/test_program_plan.py`, `tests/test_queue_dag.py`, `tests/test_start_program_alias.py`, `tests/test_cancel_program_guard.py`, `tests/test_fanout_plan.py`, `tests/test_fanout_integration.py`
- [ ] T014 Remove program/fanout cases from `tests/test_goal_tick.py`, `tests/test_cancel.py`, `tests/test_goal_engine.py`, `tests/goal_fakes.py`
- [ ] T015 Remove prose-path admission cases from `tests/test_dispatch_task.py` and single-writer-exemption warning cases from `tests/test_task_parent_goal_id.py`, `tests/test_scope_gate.py`
