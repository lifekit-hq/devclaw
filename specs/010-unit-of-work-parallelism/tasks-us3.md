---

description: "Task list for spec 010 US3 — planned `[P]` fan-out (FR-101…FR-105)"
---

# Tasks: Unit of Work & Planned Parallelism — US3 (`[P]` fan-out)

**Prerequisites**: [spec.md](./spec.md) (FR-101…FR-105), [plan-us3.md](./plan-us3.md)

**Tests**: REQUIRED — a named regression per behavior change; the zero-token
guards are load-bearing.

**Scope**: all of US3, as a two-PR stack. Increment 1 = the declared-scope
contract and its enforcement (FR-103, FR-104 enforcement, FR-105 guard).
Increment 2 = the executor that relies on it (FR-101, FR-102, FR-104
allocation, FR-105 degree). Merge in order; increment 2 is stacked on 1.

---

## Phase 1: Setup

- [X] T001 Verify the worktree import path resolves to the WORKTREE: `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` (rules/testing.md)
- [X] T002 Capture the green baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` → **2075 passed, 4 skipped**

---

## Phase 2: Increment 1 — the declared-scope contract 🎯 (branch `feat/010-us3-planned-fanout`)

**Goal**: an increment bound by a declared file scope is held to it at settle,
loudly and with zero LLM; every other increment is byte-unaffected.

### Tests — the pure substrate

- [X] T003 [P] [US3] `tests/test_declared_scope.py::test_changed_paths_reads_every_touched_path_from_the_diff` — adds, edits, deletes and renames all surface (FR-103)
- [X] T004 [P] [US3] `test_changed_paths_ignores_diff_content_that_looks_like_a_diff_header` — a repo full of patch files must not read as out-of-scope edits
- [X] T005 [P] [US3] `test_changed_paths_handles_a_path_containing_spaces`
- [X] T006 [P] [US3] `test_claimed_scopes_names_only_the_parallel_rows_this_increment_checked` (FR-101)
- [X] T007 [P] [US3] `test_a_row_already_checked_before_this_increment_is_not_a_claim`
- [X] T008 [P] [US3] `test_a_reworded_task_row_checked_in_the_same_increment_is_still_claimed` — id-keyed, matching `slice_guard`
- [X] T009 [P] [US3] `test_a_parallel_row_without_a_declared_scope_is_not_a_claim` — FR-101 is `[P]` **with** a scope
- [X] T010 [P] [US3] `test_a_scoped_row_that_is_not_marked_parallel_is_not_a_claim`
- [X] T011 [P] [US3] `test_a_scope_declaration_outside_the_task_graph_is_not_a_claim`
- [X] T012 [P] [US3] `test_parse_scopes_reads_a_comma_separated_declaration`
- [X] T013 [P] [US3] `test_star_does_not_cross_a_directory_separator_but_doublestar_does`
- [X] T014 [P] [US3] `test_a_declared_directory_covers_its_subtree` / `test_an_empty_declaration_covers_nothing`
- [X] T015 [P] [US3] `test_scope_violations_is_empty_when_every_touched_path_is_declared`
- [X] T016 [P] [US3] `test_scope_violations_names_every_out_of_scope_path` — loud and complete
- [X] T017 [P] [US3] `test_the_tasks_file_itself_is_always_in_scope_for_a_claimed_increment`
- [X] T018 [P] [US3] `test_an_increment_that_claims_nothing_is_not_consulted`
- [X] T019 [P] [US3] `test_a_garbled_diff_yields_no_claim_rather_than_a_silent_allow`
- [X] T020 [P] [US3] `test_work_smuggled_under_an_unscoped_task_still_violates_the_claim` — the #358 route-around
- [X] T021 [P] [US3] `test_a_dispatched_scope_binds_even_when_the_worker_never_checks_its_row` — the lane case
- [X] T022 [P] [US3] `test_an_empty_dispatched_scope_leaves_the_increment_unconsulted` / `test_scope_check_never_raises_on_hostile_input`

### Tests — the gate

- [X] T023 [US3] `tests/test_scope_gate.py::test_out_of_scope_increment_fails_the_gate_and_never_ships` (FR-103)
- [X] T024 [US3] `test_in_scope_increment_passes_the_gate_untouched`
- [X] T025 [US3] `test_a_dispatched_lane_scope_is_enforced_without_any_plan_bookkeeping`
- [X] T026 [US3] `test_scope_violation_blocks_under_trust_as_well_as_strict` — always-hard
- [X] T027 [US3] `test_scope_gate_runs_after_integrity_and_before_review` — a violation costs zero cognition
- [X] T028 [US3] `test_a_plan_without_declared_scopes_leaves_the_gate_chain_byte_identical` — the hard requirement
- [X] T029 [US3] `test_an_unreviewable_scope_check_fails_closed` (#186)
- [X] T030 [US3] `test_a_spec_directory_claimed_at_runtime_fails_the_declared_scope_gate` — FR-104 enforcement
- [X] T031 [US3] `test_sandbox_mcp_config_gives_the_worker_no_worker_spawn_surface` — FR-105 standing guard

### Implementation

- [X] T032 [US3] `devclaw/loom/declared_scope.py` — `changed_paths`, `parse_scopes`, `claimed_scopes`, `path_in_scope`, `scope_check`, `violation_summary`; total, never raises
- [X] T033 [US3] `"scope"` → `ALWAYS_HARD` in `devclaw/quality/gate_policy.py`, with the reason
- [X] T034 [US3] `GateInput.declared_scope` in `devclaw/quality/gate_pipeline.py`
- [X] T035 [US3] `_ScopeGate` in `devclaw/task_queue.py`, chained `verify → test_integrity → scope → [review] → browser`
- [X] T036 [P] Docs: the gate list in `docs/architecture.md`, `devclaw/quality/README.md`, `docs/flows/autonomous-issue-pipeline.md`; currency tag in `docs/INDEX.md`

**Checkpoint**: the contract is enforced. Suite 2108/4, ruff clean.

---

## Phase 3: Increment 2 — the executor (branch `feat/010-us3-fanout-scheduler`, stacked)

**Goal**: two `[P]` tasks with disjoint declared scopes execute concurrently on
one project and integrate serially onto the goal branch.

**Independent Test** (the spec's own): a plan with two `[P]` tasks with disjoint
scopes executes them concurrently, integrates serially, and a deliberately
out-of-scope edit in one increment fails that increment while the other lands.

### Tests

- [X] T037 [P] [US3] `tests/test_fanout_plan.py::test_two_parallel_tasks_with_disjoint_scopes_become_two_lanes` (FR-101)
- [X] T038 [P] [US3] `test_a_plan_with_no_parallel_markers_produces_no_fanout` — the byte-identical requirement at the decision point
- [X] T039 [P] [US3] `test_a_parallel_task_without_a_declared_scope_blocks_the_whole_fanout` — FR-101 admits only `[P]` **with** scopes
- [X] T040 [P] [US3] `test_overlapping_declared_scopes_are_refused` — hermeticity is decided before dispatch, not discovered at merge
- [X] T041 [P] [US3] `test_fanout_never_exceeds_the_host_concurrency_cap` — FR-105: degree = plan ∧ host caps
- [X] T042 [P] [US3] `test_already_checked_tasks_are_never_dispatched_again`
- [X] T043 [P] [US3] `test_the_lane_brief_pins_the_task_its_scope_and_the_allocated_spec_directory` — FR-104 allocation half
- [X] T044 [P] [US3] `test_the_lane_brief_forbids_spawning_further_agents` — FR-105 in the instruction that reaches the worker
- [X] T045 [P] [US3] `test_fanout_planning_costs_zero_cognition` — pure fs + string work
- [X] T046 [P] [US3] `tests/test_merge_queue.py::test_lanes_integrate_strictly_in_plan_order_even_when_they_finish_out_of_order` (FR-102)
- [X] T047 [P] [US3] `test_only_one_lane_integrates_at_a_time`
- [X] T048 [P] [US3] `test_a_failed_lane_releases_its_slot_instead_of_wedging_the_queue`
- [X] T049 [P] [US3] `test_a_crashing_integration_still_advances_the_queue` — fail closed, never wedge
- [X] T050 [US3] `tests/test_fanout_integration.py::test_a_lane_commits_and_merges_into_the_shared_goal_branch` — real git, tmp repos
- [X] T051 [US3] `test_a_conflicting_lane_fails_loudly_and_leaves_the_shared_branch_clean`
- [X] T052 [US3] `test_two_disjoint_lanes_integrate_serially_onto_one_goal_branch` — FR-102 end to end
- [X] T053 [US3] `test_an_out_of_scope_lane_fails_while_its_sibling_lands` — **the spec's Independent Test**
- [X] T054 [US3] `test_fanout_is_off_unless_the_operator_opts_in` — the dial, both positions

### Implementation

- [X] T055 [US3] `devclaw/goal/fanout.py` — the plan → lanes decision, the lane brief, the dial
- [X] T056 [US3] `devclaw/loom/merge_queue.py` — serial admission in plan order, always advancing
- [X] T057 [US3] `devclaw/delivery/integrate.py` — commit a lane, merge it into the shared workspace, loud on conflict
- [X] T058 [US3] `PlannedTask.workspace_dir` + `PlannedTask.lane` in `devclaw/program_plan.py`; honoured in `_persist_plan`
- [X] T059 [US3] `tasks.lane_json` column (`state_store/core.py` + `rows.py`)
- [X] T060 [US3] Lane execution in `devclaw/task_queue.py`: pin the declared scope onto the gate input, then integrate at the lane's turn before delivery
- [X] T061 [US3] `InProcessEngine.dispatch_fanout` in `devclaw/goal/engine.py` → `start_planned_program` (zero LLM)
- [X] T062 [US3] The fan-out branch of the dispatch choke point in `devclaw/goal/tick_dispatch.py`
- [X] T063 [P] Docs: fan-out + the merge queue in `docs/architecture.md`, the dial in `docs/reference/env-vars.md`, currency tags in `docs/INDEX.md`

**Checkpoint**: US3 complete — FR-101…FR-105 all shipped. Suite 2144/4, ruff clean.

---

## Phase 4: Polish & Cross-Cutting

- [X] T064 Full suite green at or above the T002 baseline (2075/4); `ruff check .` clean, both increments
- [X] T065 Open the stack per `.claude/rules/git-workflow.md`, naming the merge order; note the spec Status line the owner should change

---

## Dependencies

- Setup → Increment 1 (T003–T036) → Increment 2 (T037–T063) → Polish
- Increment 2 depends on increment 1's `declared_scope` substrate and on the
  `"scope"` gate existing — the scheduler must never run without its enforcement.
- Within each increment: tests first; inside increment 2, T055–T059 are
  independent of each other, T060 needs T056/T057/T059, T061 needs T058, T062
  needs T055/T061.

---

## Implementation Strategy

Enforcement before executor (plan-us3.md *Why this order*), and the executor
ships behind a dial that is off by default (plan-us3.md *Default stance*).
