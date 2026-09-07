# Tasks: Loop Health Metrics

**Input**: Design documents from `specs/039-loop-health-metrics/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: tripwire classes ONLY (constitution Development Workflow): FR-019 doctor seeded fault, FR-020 absent-is-never-zero, one zero-token guard case, the existing fake-agent seam proof. No behaviour tests.

**Organization**: by user story, sliced into the four stacked PRs of plan.md (A = US1+US2, B = US3+US4, C = US6, D = US5).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Verify worktree import path and green baseline (`.venv` python prints the worktree `devclaw/__init__.py`; full suite green) — no code

## Phase 2: Foundational (PR-A base)

- [X] T002 Create `devclaw/loop_health.py` (pure leaf): `CAUSES`, `bucket_for(cause)`, `derive_loop_cause(...)` per research D3, `rate math` helpers (`not_stuck_rate(buckets)` returning None on empty) — no store access
- [X] T003 Add `loop_spans`, `usage_ledger`, `intake_grades` CREATE TABLE + indexes to `devclaw/state_store/schema.py` (idempotent)
- [X] T004 Create `devclaw/state_store/health.py` `LoopHealthMixin` with `record_loop_sample(now_ms, cause, detail, max_gap_ms)` (run-length spans + `unobserved` gap per D2), `list_loop_spans(since_ms)`; compose it into `StateStore` in `devclaw/state_store/core.py`
- [X] T005 Register `loop_health` as a leaf in `pyproject.toml` `[tool.importlinter]` if the layer contract requires naming it; run `lint-imports`

## Phase 3: US1 — Know why the loop is not running (PR-A)

**Independent test**: arm no goals, tick twice, read `/loop-health.json` → `empty_backlog`; block a goal `needs_answer` → attributed to it, bucket owner, not-stuck unchanged.

- [X] T006 [US1] `devclaw/goal/engine.py`: `GoalEngine.record_loop_sample(now_ms, cause, detail)` delegating to the store with `max_gap_ms = 3 × tick_seconds` (engine learns tick seconds from config `goal_tick_seconds()`)
- [X] T007 [US1] `devclaw/goal/tick.py`: rename the body of `tick_all` to `_tick_all_pass`; new `tick_all` awaits it then calls `_record_loop_sample(engine, store, outcomes)` (best-effort, getattr seam, after every early return) which gathers pause / operator block / per-goal window / live statuses and calls `loop_health.derive_loop_cause`
- [X] T008 [US1] `devclaw/telemetry.py`: `compute_loop_health(store, window_hours)` — spans clipped to the window, per-cause/per-bucket seconds, `not_stuck_rate` (None on empty), `unobserved_seconds`, plus `clean_cycle` and `first_pass` READ with the scorecard's definitions (factor the two scorecard reads into shared helpers, no second definition)
- [X] T009 [US1] `devclaw/server/routes/observability.py`: `GET /loop-health.json?window_hours=`; `devclaw/server/tools/observability.py`: `get_loop_health` MCP tool + re-export in `devclaw/server/tools/__init__.py`
- [X] T010 [US1] `tests/test_goal_tick.py`: `test_tick_all_idle_records_one_loop_sample_with_zero_tokens` — FakeEngine gains `record_loop_sample` recording calls; assert one call, cause `all_planned_done`/`empty_backlog`, `evaluator.calls == 0`

## Phase 4: US2 — Know whether devclaw fixes itself (PR-A)

**Independent test**: seed problems with recovered/terminal counts, read the surface → rate = recovered/(recovered+terminal) with raw counts; empty catalog → null.

- [X] T011 [US2] `devclaw/telemetry.py`: `compute_self_heal(store, since_ms)` per D4 (basis string, raw sums, None on empty); include in `compute_loop_health` output
- [X] T012 [US2] `devclaw/doctor/checks_instance.py`: `check_loop_health_tables` (FAIL on missing tables/column; WARN on a silent worker usage source) + register in `INSTANCE_CHECKS`; `tests/test_doctor.py` seeded-fault test (drop `loop_spans` → FAIL with restart remedy)
- [X] T013 [US2] `tests/test_loop_health_absent_is_never_zero.py`: FR-020 parametrized cases for `compute_loop_health` (no spans), `compute_self_heal` (empty), `bucket_for` totality (unknown kind → devclaw), `not_stuck_rate` (empty → None) — PR-B/C extend this file
- [X] T014 [US2] Docs honesty PR-A: `docs/architecture.md` (observability paragraph: loop attribution + self-heal surface), `docs/runbooks/doctor.md` (check family), `docs/INDEX.md` currency tags; `/ship` PR-A

## Phase 5: US3 — Keep cost history past 30 days (PR-B)

**Independent test**: settle a task with usage, advance past retention, compact → usage row still readable; a run with no usage reads unknown; retried task sums attempts.

- [ ] T015 [US3] `devclaw/state_store/health.py`: `record_task_usage(task_id, attempt, usage|None, usage_source)`, `record_cognition_usage(trace_id, payload)`, `maybe_backfill_usage_ledger(now_ms)` (meta watermark `usage_ledger_backfilled`; traces + non-NULL result_json tasks), `usage_history(since_ms)` monthly rollup, `goal_usage_tokens(goal_id)`
- [ ] T016 [US3] `devclaw/state_store/observability.py`: `append_trace_event` writes the cognition ledger row in the same commit when `kind == 'cognition'`
- [ ] T017 [US3] `devclaw/queue/settle.py`: call `self._store.record_task_usage(task_id, attempt, result.get("usage"), ...)` right after both `self._runner(request)` sites (attempt index; 0 on the validation path)
- [ ] T018 [US3] `devclaw/goal/engine.py` + `devclaw/goal/tick.py`: `backfill_usage_ledger` seam on the cheap slot next to `_engine_prune_traces` (getattr pattern, best-effort)
- [ ] T019 [US3] `devclaw/engine/sandcastle.py`: `--tmpfs {CONTAINER_CLAUDE_DIR}/projects:rw,exec` beside the two existing scratch overlays (comment: the agent's transcript, dies with the container)
- [ ] T020 [US3] `runner/runner.py`: `_is_claude_adapter(argv)`, `_claude_transcript_usage(config_dir, workspace_dir, started_at_s)` per contracts/runner-usage.md (dedup on requestId, cwd filter, mtime filter, sidechains counted, never raises); used when `outcome.usage is None`; `usage["source"]` stamped on both paths
- [ ] T021 [US3] `devclaw/telemetry.py`: `_accum_worker`/`sum_task_usage` tolerate `cache_creation_tokens`/`source` and a missing `cost_usd`; `compute_instance_usage` gains `history` (from the ledger) with the backfill boundary note
- [ ] T022 [US3] `tests/test_loop_health_absent_is_never_zero.py`: add cases — runner `_claude_transcript_usage` on an empty dir → None and on a seeded transcript without usage → None (import runner via spec_from_file_location like `tests/test_runner_*.py`), ledger `usage_history` on unreported rows → tokens None with records/reported counts; `tests/test_runner_acp.py` fake-agent `"usage" not in result` stays green (no change)
- [ ] T023 [US3] `specs/021-worker-context-budget/contracts/runner-result.md`: usage block `source` field + transcript rule (docs honesty)

## Phase 6: US4 — Know what a shipped increment costs (PR-B)

**Independent test**: ledger rows + pr_ledger states → merged goals vs standalone PRs as separate figures, shipped-nothing total, unknown bucket, counts.

- [ ] T024 [US4] `devclaw/telemetry.py`: `compute_cost_per_outcome(store, since_ms)` per D6 (segmentation by worker dispatches behind the merged PR; unknown bucket excluded; `records`/`reported`/`tasks_without_record`)
- [ ] T025 [US4] `devclaw/telemetry.py`: REPLACE `usage.tokens_per_merged_pr` / `usage.cost_per_merged_pr_usd` in `compute_scorecard` with `usage.cost_per_outcome`; update `format_scorecard` and the `estimate_notes` wording; include the same block in `compute_loop_health`
- [ ] T026 [US4] `tests/test_loop_health_absent_is_never_zero.py`: `compute_cost_per_outcome` on an empty ledger → every `tokens_per` None, counts 0, and the scorecard no longer carries the removed keys
- [ ] T027 [US4] Docs honesty PR-B: `docs/architecture.md` (usage ledger + cost per outcome), `docs/flows/task-execution.md` (runner result usage source, the sandbox tmpfs), `docs/INDEX.md`; `/ship` PR-B (stacked on A)

## Phase 7: US6 — Estimate calibration (PR-C)

**Independent test**: grade an issue → `intake_grades` row; close a goal from it → convergence row carries both predictions + dispatches + rounds; `/calibration.json` reports not-determinable below the floor.

- [X] T028 [US6] `devclaw/state_store/health.py`: `record_intake_grade(repo, issue_number, readiness, claimed, assessed, sizing, stale)`, `intake_grade(repo, issue_number)`, `count_ready_issues_without_goal()` (join against `goal_issue_identity`)
- [X] T029 [US6] `devclaw/intake.py`: `grade_and_label(..., record=None)` calls `record({...})` after labelling; thread `record` through `regrade`, `grade_backlog`, `recover_pending_grades`; `devclaw/server/tools/intake.py` (+ the webhook route if it calls regrade) pass `record=store.record_intake_grade`
- [X] T030 [US6] `devclaw/goal/state.py`: ALTER `goal_convergence` ADD the six columns; `devclaw/goal/state_status.py` `record_convergence` accepts and writes them; `devclaw/goal/store/status.py` `record_convergence` computes predictions (intake_grades over `goal.issue_refs` × `repo_slug(goal.repo_url)`), `dispatches` (tasks of the goal, `kind != review_repository`), `steered` (human steering rows), `cost_tokens` (ledger — DEFERRED with US3, column not added)
- [X] T031 [US6] `devclaw/telemetry.py`: `compute_calibration(store)` per D7 (`CALIBRATION_MIN_SAMPLE = 10`); `devclaw/server/routes/observability.py`: `GET /calibration.json`
- [X] T032 [US6] `devclaw/goal/engine.py` + `tick.py` + `loop_health.py`: `no_goal_armed` emission when no live goal exists and `count_ready_issues_without_goal() > 0`
- [ ] T033 [US6] `tests/test_loop_health_absent_is_never_zero.py`: calibration below the floor → `determinable=false`, predictors None, `needed` stated; a convergence row without predictions still records actuals; `tests/test_doctor.py` seeded fault extends to the missing `cost_tokens` column
- [ ] T034 [US6] Docs honesty PR-C: `docs/reference/intake-shape.md` (grade persisted), `docs/architecture.md`, `docs/INDEX.md`; `/ship` PR-C (stacked on B)

## Phase 8: US5 — See it without asking (PR-D)

**Independent test**: console overview shows the five metrics; a metric with no data reads `—`/"no data"; usage page renders months past retention.

- [ ] T035 [US5] `console/src/api.ts`: `LoopHealth`, `CostPerOutcome`, `UsageHistory` types; `fetchLoopHealth()`, `fetchCalibration()`; `InstanceUsage.history`
- [ ] T036 [US5] `console/src/pages/Overview.tsx`: "Loop health" strip — not-stuck rate + three buckets, self-heal, clean-cycle, first-pass, cost per merged goal / standalone PR; `null` → `—` with a "no data" caption
- [ ] T037 [US5] `console/src/pages/Usage.tsx`: monthly trend list/bars from `history`, with the backfill boundary note; unknown months distinct from zero
- [ ] T038 [US5] `cd console && npm ci && npm run build` (tsc gate) — no dist committed
- [ ] T039 [US5] Docs honesty PR-D + spec Status header update to what shipped; `/ship` PR-D (stacked on C)

## Dependencies

- T002–T005 → T006–T014 (PR-A) → T015–T027 (PR-B: the ledger feeds cost; the runner source feeds the ledger) → T028–T034 (PR-C: `cost_tokens` reads the ledger; `no_goal_armed` reads `intake_grades`) → T035–T039 (PR-D surfaces all of it)
- Within PR-B: T019/T020 (runner/sandbox) are independent of T015–T018 (host ledger) — [P]
- Within PR-C: T028/T029 (grade persistence) before T030 (close-time join)

## Implementation strategy

PR-A first (the only unmeasurable-today metric); land the ledger + its worker source next because every later number joins on it; calibration third; the console last because it only displays.
