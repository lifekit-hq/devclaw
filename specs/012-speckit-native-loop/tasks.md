# Tasks: Speckit-Native Amputation

**Feature**: `012-speckit-native-loop` | **Branch**: `refactor/speckit-native-amputation`
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

**Owner ruling**: this lands as ONE PR. The phases below are the commit-series
order inside it, chosen so a mistake surfaces close to its cause.

**Test policy**: this arc DELETES tests belonging to deleted mechanisms and ADDS
three named regression tests for the three behavior changes. The gate is **zero
failures**, not count parity — see `baseline.md`.

---

## Phase 1: Setup

- [ ] T001 Verify the import path resolves to the worktree: `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` from the worktree root
- [ ] T002 Confirm the recorded baseline still reproduces at HEAD in specs/012-speckit-native-loop/baseline.md (1990 passed, 4 skipped)

---

## Phase 2: Foundational (blocking — read before deleting anything)

- [ ] T003 Read the retention rule in specs/012-speckit-native-loop/data-model.md: `tasks.program_id` stays populated in history and the `program_id IS NULL` guard in `list_pending_standalone` MUST survive the cut
- [ ] T004 Confirm no gate is in the cut set — `devclaw/quality/evals.py` and `eval_judge.py` are offline analysis, not gates consulted by `gate_pipeline.py`

---

## Phase 3: User Story 1 — The tree carries one vocabulary (P1)

**Goal**: every pre-speckit mechanism deleted, suite green, no production reference remaining.

**Independent test**: `pytest -q` → zero failures; `grep` finds no dispatch path, model, prompt or tool referencing the removed concepts.

### 3a. Leaf deletions (no in-repo importer)

- [ ] T005 [P] [US1] Delete devclaw/engine/claude_sdk.py and its wiring branch in devclaw/server/_state.py
- [ ] T006 [P] [US1] Delete devclaw/prompts/sdk-implement-feature.md, sdk-fix-bug.md, sdk-review-repository.md, sdk-onboard.md
- [ ] T007 [P] [US1] Delete tests/test_claude_sdk_engine.py
- [ ] T008 [P] [US1] Delete devclaw/quality/eval_judge.py, devclaw/quality/prompts/eval-judge.md, tests/test_eval_judge.py
- [ ] T009 [P] [US1] Delete devclaw/quality/evals.py and remove its export from devclaw/quality/__init__.py
- [ ] T010 [P] [US1] Delete devclaw/engine/project_image.py and tests/test_project_image.py
- [ ] T011 [US1] Run the suite; resolve any import fallout from T005–T010

### 3b. Trend stack

- [ ] T012 [US1] Delete devclaw/trend_detector.py, devclaw/trend_signals.py, devclaw/bookmark.py
- [ ] T013 [US1] Remove the trend wiring from devclaw/goal/service.py (`_trend`, the lazy constructor, DEVCLAW_TREND_ENABLED reference)
- [ ] T014 [US1] Remove the `review_trends` tool from devclaw/server/tools.py
- [ ] T015 [P] [US1] Delete tests/test_trend_detector.py and tests/test_trend_signals.py
- [ ] T016 [P] [US1] Remove the DEVCLAW_TREND_* group from docs/reference/env-vars.md and .env.example
- [ ] T017 [US1] Run the suite; confirm the zero-token guard tests in tests/test_goal_tick.py stay green

### 3c. Program vocabulary — whole functions first

- [ ] T018 [US1] Delete the 8 program functions in devclaw/task_queue.py: `_no_host_planner`, `cancel_program`, `submit_program`, `_maybe_terminalize`, `_schedule_program`, `_persist_plan`, `start_planned_program`, `_plan_and_start`
- [ ] T019 [US1] Delete the 10 program methods in devclaw/state_store/core.py: `cancel_program_pending_tasks`, `create_program`, `mark_program_running`, `mark_program_done`, `mark_program_failed`, `mark_program_cancelled`, `list_programs`, `latest_program_for_goal`, `get_program`, `list_program_tasks`, `list_nonterminal_programs`
- [ ] T020 [US1] Leave the `programs` DDL in `_bootstrap` untouched (plan.md Complexity Tracking) — fresh and live databases keep one schema shape
- [ ] T021 [US1] Delete `_notify_program` from devclaw/task_notify.py
- [ ] T022 [US1] Delete `_poll_program` and `latest_program_for_goal` from devclaw/goal/engine.py, and the `action.tool == "start_program"` dispatch branch
- [ ] T023 [US1] Delete the `get_program`, `list_programs`, `cancel_program`, `start_program` tools from devclaw/server/tools.py
- [ ] T024 [US1] Delete the `/dashboard/{program_id}` and `/programs/{program_id}/events` routes from devclaw/server/http.py
- [ ] T025 [US1] Delete devclaw/program_plan.py

### 3d. Program vocabulary — surgical call-site edits

- [ ] T026 [US1] devclaw/task_queue.py: remove program branches from `_pump`, `_execute`, `_launch`, `recover`, `cancel_task`, `_run_and_settle`, `_append_task_event`, `_check_and_trip_breaker`, `__init__`
- [ ] T027 [US1] devclaw/state_store/core.py: remove program handling from `create_task`, `mark_done`, `latest_task_for_goal`, `_insert_live_outcome`, `append_event`, `list_events`, `has_active_work` — **do NOT touch the `program_id IS NULL` guard in `list_pending_standalone`**
- [ ] T028 [US1] devclaw/state_store/rows.py: delete the `Program` dataclass and `_row_to_program`; leave the program-era columns on `Task`
- [ ] T029 [US1] devclaw/goal/models.py: remove the program members from `InFlight` and `PollResult`
- [ ] T030 [US1] devclaw/goal/tick_settle.py: remove program handling from `_readopt_orphaned_ref`, `_readopt_ref`, `_resolve_polling_action`
- [ ] T031 [US1] devclaw/goal/reconcile.py: remove the program branch from `reconcile_stack`
- [ ] T032 [P] [US1] Delete tests/test_program_plan.py, test_start_program_alias.py, test_state_program.py, test_cancel_program_guard.py
- [ ] T033 [US1] Add the named regression test `test_pending_task_with_program_id_is_never_claimed` to tests/test_task_retry.py
- [ ] T034 [US1] Run the suite; resolve fallout

### 3e. Superseded singles

- [ ] T035 [US1] Delete devclaw/goal/repo_brief.py, the brief-prefix block in devclaw/goal/tick_dispatch.py (lines ~171-186), the repo-notes merge in devclaw/goal/tick_settle.py (~298-305), and the `repo_brief` kind from devclaw/goal/state.py PROJECT_DOC_KINDS
- [ ] T036 [P] [US1] Delete tests/test_repo_brief.py
- [ ] T037 [US1] Delete devclaw/goal/self_issue.py, its call site at the cycle-report close, and tests/test_self_issue.py
- [ ] T038 [US1] Delete devclaw/goal/merge.py, its call site in devclaw/goal/service.py, the DEVCLAW_GOAL_AUTOMERGE env, and tests/test_goal_merge.py
- [ ] T039 [US1] Delete devclaw/elicitation.py, devclaw/prompts/scope-grill.md, scope-grill-contract.md, the `scope_grill` tool, and tests/test_elicitation.py
- [ ] T040 [US1] Delete the PLAN.md half of devclaw/goal/slice_guard.py: `_milestone_states`, `count_milestone_flips`, `_plan_at_ref_sync`, `mega_dump_flips_sync` and their module constants
- [ ] T041 [US1] Run the suite; resolve fallout

---

## Phase 4: User Story 2 — One entry point (P1)

**Goal**: the legacy dispatch tools file one-shot goals; `queue.submit()` has no caller outside the goal layer.

**Independent test**: calling `fix_bug` returns a goal id and runs the identical path; `grep` shows no `queue.submit(` outside `devclaw/goal/`.

- [ ] T042 [US2] Rewrite `dispatch_task`, `implement_feature`, `fix_bug`, `review_repository` in devclaw/server/tools.py as sugar over `goals.create_goal(mode='one_shot')`, following the `start_program` precedent at tools.py:519
- [ ] T043 [US2] Rewrite the `onboard` tool in devclaw/server/tools.py the same way; keep `kind="onboard"` usable by devclaw/speckit_setup.py
- [ ] T044 [US2] Confirm `queue.submit()` has no caller outside devclaw/goal/ and devclaw/speckit_setup.py
- [ ] T045 [US2] Add the named regression test `test_legacy_dispatch_tools_file_one_shot_goals` to tests/test_start_program_alias.py's replacement (rename to tests/test_dispatch_tool_sugar.py)
- [ ] T046 [US2] Run the suite

---

## Phase 5: User Story 3 — The brief carries payload, never doctrine (P1)

**Goal**: no dispatch text can reach a PR title or body; the brief stops duplicating the worker skill.

**Independent test**: the three regression tests below pass; a rendered brief carries payload only.

- [ ] T047 [US3] Trim `_advance_brief` in devclaw/goal/tick.py to the marker line plus Goal / Done when / failure context / steering — delete the four speckit instruction paragraphs
- [ ] T048 [US3] Change `is_advance_brief` in devclaw/advance_brief.py from `strip().startswith(MARKER)` to a containment match so no prefix can defeat it
- [ ] T049 [US3] Pass the display form to delivery: set the task row `title` at dispatch in devclaw/goal/tick_dispatch.py so `deliver_change(title=...)` outranks the raw goal text (devclaw/delivery/__init__.py:517)
- [ ] T050 [US3] Update tests/test_delivery.py fixtures to build `_ADVANCE_BRIEF` through the real generator shape, not a hand-written string starting at the marker
- [ ] T051 [US3] Add the named regression test `test_prefixed_advance_brief_never_reaches_pr_title_or_body` to tests/test_delivery.py
- [ ] T052 [US3] Add the named regression test `test_advance_brief_detected_with_any_prefix` to tests/test_advance_brief_speckit.py
- [ ] T053 [US3] Run the suite

---

## Phase 6: Polish & Cross-Cutting

- [ ] T054 [P] Delete .claude/rules/cognition-prompts.md
- [ ] T055 [P] Update CLAUDE.md: the layer map, the module tree, and any mention of programs, trends, repo brief or scope grill
- [ ] T056 [P] Update docs/architecture.md and docs/flows/task-execution.md for the removed dispatch path
- [ ] T057 [P] Update docs/INDEX.md currency tags for every doc touched
- [ ] T058 Run the full suite; record the final count against baseline.md and confirm zero failures
- [ ] T059 Run `DEVCLAW_ENGINE=stub .venv/bin/python evals/run_all.py`
- [ ] T060 Walk specs/012-speckit-native-loop/quickstart.md end to end
- [ ] T061 Compose the PR body with the complete removal inventory from specs/012-speckit-native-loop/contracts/mcp-surface.md (FR-020)

---

## Deferred — NOT in this PR

- **US4 (P2)** one contract of record — carries the Principle V amendment; two open `[NEEDS CLARIFICATION]` markers (FR-017, FR-018)
- `devclaw/delivery/deploy.py`, `delivery/repo.py`, the 6 deploy/repo tools
- `devclaw/goal/triage.py` + prompts/self-triage.md
- The schema-drop migration for orphaned tables and columns

---

## Dependencies

```
Phase 1 (T001-T002)
   ↓
Phase 2 (T003-T004)  ← read-only, blocking
   ↓
Phase 3a → 3b → 3c → 3d → 3e     US1, strictly ordered (3c before 3d)
   ↓
Phase 4 (US2)   ← needs US1's tools.py edits landed
   ↓
Phase 5 (US3)   ← independent of US2; can precede it
   ↓
Phase 6 (polish)
```

**Parallel opportunities**: T005–T010 (leaf deletes, different files) ·
T015–T016 · T032/T036 · T054–T057 (docs, different files).

**Serial by necessity**: all of 3c and 3d — they touch the same four large files.

## Implementation Strategy

US1 alone is the MVP: the tree carries one vocabulary and the suite is green.
US2 and US3 are small, independently valuable, and land in the same PR by owner
ruling. Run the suite at every phase boundary (T011, T017, T034, T041, T046,
T053, T058) so a regression is bounded by one phase, not by the whole diff.
