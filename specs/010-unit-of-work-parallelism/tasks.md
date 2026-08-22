---

description: "Task list for spec 010 P1 — single writer per project"
---

# Tasks: Unit of Work & Planned Parallelism — P1 (single writer per project)

**Prerequisites**: plan.md, spec.md (FR-005 amended 2026-08-22 — derived hold)

**Tests**: REQUIRED — constitution mandates a named regression per behavior
change; the zero-token guards are load-bearing.

**Scope**: P1 (FR-001…FR-009). P3 fan-out is out of slice.

---

## Phase 1: Setup

- [X] T001 Verify the worktree import path resolves to the WORKTREE: `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` (rules/testing.md)
- [X] T002 Capture the green baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q`

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Add `goal_created_at_ms_map()` to `devclaw/goal/state.py` — one grouped `SELECT goal_id, MIN(ts) FROM goal_log GROUP BY goal_id`, the age source (no schema change; `create_goal` writes a "goal created" row). *Corrected during implementation*: the column is `ts`, not `created_at`; the first draft queried a non-existent column and a defensive `except` in `holder_map` swallowed it, silently degrading the fleet to id-ordering — the swallow was removed (see T018).
- [X] T004 Expose it as a read-only `GoalStore.goal_created_at_map()` in `devclaw/goal/store/content.py`
- [X] T005 Add `Outcome.QUEUED` to `devclaw/goal/tick_context.py` with a comment marking it a zero-token, zero-write outcome

**Checkpoint**: the derivation has an age source and an outcome to report.

---

## Phase 3: User Story 1 — Single writer per project (Priority: P1) 🎯 MVP

**Goal**: at most one goal per project dispatches; the rest queue and start
automatically when the holder goes terminal.

**Independent Test**: two goals on one project + one on a second project — only
the first same-project goal and the other-project goal dispatch; complete the
first and the queued one starts on a later tick with no operator action.

### Tests for User Story 1

- [X] T006 [P] [US1] Create `tests/test_project_hold.py::test_holder_is_the_oldest_non_terminal_goal_on_the_project` — the derivation picks oldest-by-age, tie-broken on goal id (FR-001, FR-005)
- [X] T007 [P] [US1] Add `test_terminal_goals_never_hold_a_project` — done/cancelled goals drop out of the derivation, so the next goal becomes holder with nothing released (FR-003, cancel edge case)
- [X] T008 [P] [US1] Add `test_blocked_holder_keeps_the_project` — a blocked (non-terminal) goal still holds (FR-008)
- [X] T009 [P] [US1] Add `test_goals_on_distinct_projects_each_hold_their_own` — the hold is per-project, never global (FR-001, SC-005)
- [X] T010 [P] [US1] Add `test_goal_without_a_project_scope_is_never_queued` — a goal with no project_id and no workspace_dir contends for nothing
- [X] T011 [P] [US1] Add `test_waiting_reason_names_the_holding_goal` — the derived reason identifies the holder (FR-002, SC-006)
- [X] T012 [US1] Add `test_second_goal_on_one_project_is_queued_and_dispatches_nothing` to `tests/test_goal_tick.py` — acceptance 1 (FR-001/FR-002)
- [X] T013 [US1] Add `test_queued_goal_starts_automatically_after_the_holder_goes_terminal` to `tests/test_goal_tick.py` — acceptance 3, no operator action (FR-003, SC-003)
- [X] T014 [US1] Add `test_queued_goal_tick_spends_zero_cognition_and_does_not_churn_state` to `tests/test_goal_tick.py` — `FakeClaude.calls == 0`, no dispatch, and no state/log change across repeated ticks (FR-004, SC-004). *Revised during implementation*: a strict no-write assertion was wrong — every goal's FIRST tick seeds the no-progress watchdog (column-only telemetry, unrelated to the hold). The property that matters is no CHURN, measured from the second tick on.
- [X] T015 [US1] Add `test_goal_on_another_project_dispatches_while_one_project_is_held` to `tests/test_goal_tick.py` — acceptance 2 (SC-005)
- [X] T016 [US1] Add `test_queued_goal_still_settles_in_flight_work` to `tests/test_goal_tick.py` — the upgrade edge case: nothing is orphaned, but nothing new dispatches
- [X] T017 [US1] Add `test_get_goal_surfaces_the_queued_wait_with_its_holder` and `test_get_goal_shows_no_queue_once_the_holder_is_terminal` to `tests/test_goal_diagnosis_surfaces.py` (the nearest service-surface module) — SC-006, no log-diving

### Implementation for User Story 1

- [X] T018 [US1] Create `devclaw/goal/project_hold.py` — `scope_key(goal)` (project_id, else normalized workspace_dir, else None), `holder_map(store)` (scope → holder goal id; non-terminal only; ordered by age then goal id), `waiting_reason(holder_id)`. Failure policy is deliberately narrow: an unloadable `goal.yaml` is skipped, everything else RAISES. An empty map does not mean "be careful", it means "nothing is held" — so a swallowed store error would silently switch the whole invariant off.
- [X] T019 [US1] Thread the holder map through `devclaw/goal/tick.py`: compute ONCE per `tick_all` sweep and pass into `tick_goal`; `tick_goal` computes it lazily when called directly (tests / single-goal path)
- [X] T020 [US1] Add the dispatch gate in `_handle_long_lived_advance` — AFTER the settled-ok done-gate branch, BEFORE the steering read: a non-holder returns `Outcome.QUEUED` having spent no cognition and written nothing (FR-001, FR-004)
- [X] T021 [US1] Derive the waiting reason in `GoalService.get_goal` (`devclaw/goal/service.py`) so a queued goal names its holder on its own status surface (FR-002, SC-006)
- [X] T022 [US1] FR-009: in `devclaw/server/tools.py`, add a loud warning to the `dispatch_task` / `fix_bug` / `implement_feature` response when a goal holds the target project — the dispatch still proceeds (operator-present exemption)

**Checkpoint**: P1 functional and independently testable.

---

## Phase 4: Polish & Cross-Cutting

- [X] T023 [P] FR-007: adopt the canonical terminology (work item / saga / task graph / increment-as-Unit-of-Work) and the derived project hold in `docs/architecture.md`; update its currency tag in `docs/INDEX.md`
- [X] T024 Full suite green at or above the T002 baseline; every pre-existing zero-token guard test still passes
- [X] T025 Update the spec Status line and open the PR per `.claude/rules/git-workflow.md`; note that #553 closes referencing this spec (FR-006) once merged

---

## Dependencies

- Setup (T001–T002) → Foundational (T003–T005) → US1 (T006–T022) → Polish (T023–T025)
- Within US1: tests first; T018 before T019/T020 (they import it); T020 after T019 (needs the threaded map); T021 and T022 are independent of the tick gate and of each other
- T006–T011 share one new file — write in one pass; T012–T017 touch two existing test modules

---

## Implementation Strategy

MVP = this whole slice. FR-001…FR-009 are all P1-firm; the `[P]` fan-out
(FR-101…FR-105) is explicitly out and must not be started here.

---

## Deviations recorded during implementation

Three, all pinned by tests and explained above: the age column is `ts` not
`created_at`; `holder_map` fails loud instead of swallowing store errors; and
the zero-cost assertion is no-churn rather than no-write. A fourth is a test
fixture note — `seed_goal` defaults every goal to `/repos/demo`, so goals in a
multi-goal test now contend for one project unless given distinct workspaces
(three existing tests were corrected, and the fixture docstring warns about it).
