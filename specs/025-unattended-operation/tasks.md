# Tasks: Unattended-Week Operation

**Input**: Design documents from `/specs/025-unattended-operation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/operator-surface.md

**Tests**: REQUIRED — repo rule: every behavior change ships a named
regression test, tests written first (red) per increment. Zero-token guard
tests are load-bearing and never edited to pass.

**Organization**: three story phases = three PRs, in order. US1 is the MVP
and the largest; US2/US3 are independent of each other but both land after
US1's close-path refactor.

## Phase 1: Setup

- [X] T001 Verify worktree import path resolves to the worktree, not the main checkout: `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` (rules/testing.md)
- [X] T002 Capture the green baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` — record pass count for the PR descriptions

## Phase 2: Foundational (blocking for all stories)

- [X] T003 Add `pending_merge_pr: str = ""` and `merge_heal_attempted: bool = False` to `GoalStatus` in devclaw/goal/models.py, with store round-trip (columns + read/write) in devclaw/goal/store/ — named test `test_goal_status_merge_fields_roundtrip` in tests/test_goal_store.py
- [X] T004 [P] Doctor check for the new state shape (no goal `done` with `pending_merge_pr` set; columns present) in devclaw/doctor/checks_instance.py + seeded-fault test in tests/test_doctor_instance.py (spec 016 FR-014)

## Phase 3: US1 — merge-on-close + conflict self-heal + lane skip-over (P1) 🎯 MVP

**Goal**: an `achieved` verdict ends with the cumulative PR squash-merged
into the default branch, with one bounded pipeline-dispatched conflict heal;
a hard failure parks `mechanical:merge_failed`; resume retries the merge
only; a blocked goal releases its project lane.

**Independent test**: stubbed-forge close drives — see quickstart US1 block.

### Tests first (red)

- [X] T005 [P] [US1] `test_achieved_close_squash_merges_the_cumulative_pr_before_done` — fake gh records `pr merge --squash` BEFORE the ACHIEVE transition; ping carries merged sha — in tests/test_merge_on_close.py (new file; fixture shape from tests/goal_fakes.py seed_goal + a RecordingMergeRunner)
- [X] T006 [P] [US1] `test_merge_conflict_dispatches_one_resolution_increment_then_parks` — CONFLICT → one implement_feature dispatch whose brief names the goal branch + default head + "resolve conflicts"; second CONFLICT → blocked `mechanical:merge_failed`; never a third dispatch — in tests/test_merge_on_close.py
- [X] T007 [P] [US1] `test_already_merged_pr_at_close_is_success` + `test_closed_unmerged_pr_parks_loudly` (FR-004 both halves) — in tests/test_merge_on_close.py
- [X] T008 [P] [US1] `test_resume_after_merge_failure_retries_merge_without_done_gate` — resume → merge re-attempt, `FakeClaude.calls == 0`, success closes DONE (FR-003) — in tests/test_merge_on_close.py
- [X] T009 [P] [US1] `test_blocked_goal_releases_project_lane_for_queued_successor` — blocked predecessor + queued successor on one project → successor dispatches next tick (FR-015) — in tests/test_goal_tick.py beside the project-hold cases
- [X] T010 [P] [US1] `test_forge_error_at_close_parks_after_bounded_retries` + `test_merge_failure_never_wedges_other_goals` (tick continues; other goal settles) — in tests/test_merge_on_close.py

### Implementation

- [X] T011 [US1] New module devclaw/goal/merge_on_close.py: `MergeOutcome` enum (MERGED/ALREADY_MERGED/CONFLICT/CLOSED_UNMERGED/ERROR), `attempt_merge(pr_url) -> MergeOutcome` shelling `gh pr merge --squash` + `gh pr view --json state,mergedAt` for idempotence; `_run_gh` conventions copied from devclaw/goal/mergeability.py (which stays read-only); injectable runner for tests
- [X] T012 [US1] Wire the merge step into `_resolve_done_gate` in devclaw/goal/tick_donegate.py between the CI-rewrite stage and the ACHIEVE transition (:474): merged → existing close flow (+ merged sha in ping + best-effort workspace sync to default head); CONFLICT+budget → set `merge_heal_attempted`, persist `pending_merge_pr`, dispatch the resolution increment via `_dispatch_action`; else → BLOCK `mechanical:merge_failed` with PR-naming `blocked_on`
- [X] T013 [US1] Pending-merge resume branch in `_handle_long_lived_advance` in devclaw/goal/tick.py — before brief construction: `pending_merge_pr` set → re-attempt merge (zero cognition), success → ACHIEVE, failure → re-park; clear both fields on success; `steer_goal` in devclaw/goal/service.py resets `merge_heal_attempted`
- [X] T014 [US1] Lane skip-over: `phase="blocked"` does not occupy the project lane in devclaw/goal/project_hold.py; keep queued_reason surfaces honest (update text if it names the blocked goal)
- [X] T015 [US1] Constitution amendment (same PR): Principle V trust rationale → done-gate is the merge authority, human reviews post-merge, revert is the remedy — in .specify/memory/constitution.md (version bump + amendment note); cross-check CLAUDE.md Hardening section stays consistent, adjust the "human reviews every PR" clauses in the same commit
- [X] T016 [US1] Docs honesty: update devclaw/goal/mergeability.py module docstring (the #641 tombstone gains a "partially reversed by spec 025 at exactly one seam" paragraph), docs/architecture.md + docs/flows/task-execution.md close-path description, docs/INDEX.md currency tags
- [X] T017 [US1] Full gate: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy` — count ≥ baseline + new tests; open PR 1

**Checkpoint**: US1 alone is the functional MVP of the unattended week.

## Phase 4: US2 — devclaw self-deploy with probe + rollback (P2)

**Goal**: a devclaw-repo merge redeploys the instance, quiescence-gated,
probe-checked, one auto-rollback.

**Independent test**: quickstart US2 block (stubbed trigger + `bash -n`).

### Tests first (red)

- [ ] T018 [P] [US2] `test_devclaw_repo_merge_records_deploy_pending_and_waits_for_quiescence` — trigger fires only at `count_running() == 0` (running-only, NOT `has_active_work`); running task defers — in tests/test_self_deploy_trigger.py (new file)
- [ ] T019 [P] [US2] `test_deploy_pending_expires_loudly_after_bounded_wait` + `test_non_devclaw_merge_never_triggers_deploy` — in tests/test_self_deploy_trigger.py
- [ ] T020 [P] [US2] `test_deploy_trigger_is_zero_token_on_idle_ticks` — `FakeClaude.calls == 0` with `deploy_pending` set — in tests/test_self_deploy_trigger.py

### Implementation

- [ ] T021 [US2] Meta verbs `set_deploy_pending`/`deploy_pending`/`record_deploy_last` in devclaw/state_store/control.py (operator_hold shape: absence==off, corrupt==off) + config knob `DEVCLAW_DEPLOY_QUIESCENCE_S` (default 21600) in devclaw/config.py + docs/reference/env-vars.md row
- [ ] T022 [US2] Close-path hook (devclaw-repo match on project repo_url) records `deploy_pending` in devclaw/goal/tick_donegate.py; tick-path mechanical quiescence check + `gh workflow run deploy.yml` trigger + expiry in devclaw/goal/tick.py (after the cheap guards, before any cognition)
- [ ] T023 [P] [US2] deploy/deploy-devclaw-auto.sh (new): capture running sha from /health, run deploy-devclaw.sh with the new tag, on health-gate failure re-run once with the captured sha, fire the notify-relay ping on rollback failure, exit codes per contracts/operator-surface.md; `bash -n` clean
- [ ] T024 [P] [US2] .github/workflows/deploy.yml: add the auto lane input calling the wrapper; keep `workflow_dispatch` manual lane byte-compatible
- [ ] T025 [US2] Docs: docs/runbooks/ self-deploy section + env-vars + INDEX currency tags; full gate; open PR 2

## Phase 5: US3 — quiet mode (P3)

**Goal**: armed quiet mode sends only instance-dead pings; everything else
recorded and readable on return.

**Independent test**: quickstart US3 block.

### Tests first (red)

- [ ] T026 [P] [US3] `test_quiet_mode_suppresses_and_records_all_noncritical_pings` — one event per ping class INCLUDING the cycle report (the service.py:510 direct-send path); only send_critical reaches the wire — in tests/test_quiet_mode.py (new file)
- [ ] T027 [P] [US3] `test_auth_pause_ping_pierces_quiet_mode` + `test_quiet_mode_expiry_self_disarms` + `test_suppressed_backlog_reads_back_in_order` — in tests/test_quiet_mode.py

### Implementation

- [ ] T028 [US3] `suppressed_pings` table in devclaw/state_store/schema.py + insert/read in a state_store mixin + doctor check in devclaw/doctor/checks_instance.py (+ seeded-fault test)
- [ ] T029 [US3] `QuietNotifier` decorator (send suppress+record while armed w/ lazy expiry; send_critical always delegates) in devclaw/goal/notify.py; quiet_mode meta verbs in devclaw/state_store/control.py; bind the wrapper at the notifier binding in devclaw/goal/service.py (:132-134)
- [ ] T030 [US3] Route the instance-dead class through send_critical: the auth-pause owner ping in devclaw/goal/tick.py (:887-909) — narrow change, the episode-classification logic untouched
- [ ] T031 [US3] MCP verb `set_quiet_mode(on, until?, reason?)` in devclaw/server/tools/control.py + export in devclaw/server/tools/__init__.py + `get_status` gains the quiet_mode block + backlog read surface per contracts/operator-surface.md — named test `test_set_quiet_mode_tool_roundtrip` in tests/test_run_schedule_tool.py's module or a sibling
- [ ] T032 [US3] Docs (env-vars if any knob, INDEX tags); full gate; open PR 3

## Phase 6: Polish

- [ ] T033 Live legs on the VPS after deploy, per quickstart "Live legs": one throwaway goal closes AND merges untouched; successor-starts-from-merged-head check; one probe-failure rollback drill
- [ ] T034 Update the finance-sentry project notes via update_project (merge half of the 2026-07-19 pin lifted by spec 025; deploy half stands) so the registry note stops contradicting live behavior

## Dependencies

- Phase 2 (T003-T004) blocks T012/T013 (fields) — everything else in US1 can start in parallel.
- US1 (Phase 3) blocks US2 and US3 phases only at the close-path wiring (T022 touches tick_donegate after T012); tests can be written any time.
- US2 ∥ US3 — no shared files except tick.py (T022 vs T030 — coordinate, land sequentially).

## Parallel example (US1)

T005-T010 are six [P] test tasks across two files — write together (red),
then T011 (new module) ∥ T014 (project_hold) ∥ T015/T016 (docs), then T012 →
T013 sequentially (both touch the tick close path).

## Implementation strategy

MVP = Phase 1-3 (US1) as PR 1 — this alone makes the unattended week
function. US2 (PR 2) and US3 (PR 3) follow the same day. Each PR: tests
first, full suite + ruff + mypy green, docs in the same PR, squash merge.
