# Tasks: Scorecard Measures the Ratchet

**Input**: Design documents from `/specs/018-scorecard-ratchet/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/scorecard-output.md

**Tests**: REQUIRED — the constitution mandates a named regression test per
behavior change, and FR-009 makes the audit-week shapes seeded fixtures.

**Organization**: One phase per user story; each story lands as ONE
reviewable PR (constitution workflow rule). The whole spec is the
commitment.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Record the green suite baseline (full `pytest -q` count) and verify `ruff check .` + `mypy` clean before any change, per .claude/rules/testing.md

---

## Phase 2: Foundational

**Purpose**: shared seeded-store fixture used by every story's metric tests

- [X] T002 Add scorecard seeding helpers (seed goal_convergence/pr_ledger/goal_steering/cycle_reports rows into a temp devclaw.db StateStore) in tests/scorecard_fixtures.py, following the tests/goal_fakes.py conventions

**Checkpoint**: fixtures importable; no production code touched yet

---

## Phase 3: User Story 1 — Per-goal convergence (Priority: P1) 🎯 MVP

**Goal**: rounds survive the close; scorecard reports first-pass rate,
rounds distribution, abandoned and rounds-unknown buckets — goal-weighted.

**Independent Test**: seed three goals (first-pass close, 3-round close,
pre-proposal cancel) → scorecard reports first_pass 1/2, median 2,
abandoned 1; a goal closed with no convergence row lands in rounds_unknown.

- [X] T003 [US1] Add `goal_convergence` table to devclaw/state_store/schema.py per data-model.md (idempotent CREATE + lazy ALTER pattern used by the sibling tables)
- [X] T004 [US1] Add `record_goal_close(goal_id, outcome, rounds, workspace_dir, closed_at_ms)` writer + a window reader beside the eval_outcomes methods in devclaw/state_store/evals.py
- [X] T005 [US1] Write the achieved-close convergence row (rounds = status.donegate_rounds + 1 semantics per the round counter's meaning at that site) BEFORE the status reset in devclaw/goal/tick_donegate.py, threading the StateStore handle the cycle-report step already proves GoalService holds
- [X] T006 [US1] Write `abandoned` convergence rows at the goal-cancel sites in devclaw/goal/service.py (the two paths that currently zero donegate_rounds), preserving their CAS semantics
- [X] T007 [US1] Replace `first_pass_hit_rate` with the `convergence` block in devclaw/telemetry.py::compute_scorecard — goal-weighted rates, null-on-zero-denominator, rounds_unknown via terminal goal_status/goal_phase_history rows lacking a convergence row, bench-project goals excluded via the registry attribution pattern already in compute_instance_usage
- [X] T008 [US1] Update format_scorecard rendering for the convergence block in devclaw/cli.py
- [X] T009 [P] [US1] Named regression tests for the writers in tests/test_goal_convergence_record.py: test_close_records_rounds_before_reset, test_cancel_records_abandoned, test_pre_proposal_cancel_records_rounds_zero, test_churn_brake_goal_records_final_round_count
- [X] T010 [P] [US1] Named seeded-metric tests in tests/test_telemetry_scorecard.py: test_first_pass_weighs_goals_not_verdicts (SC-003, 6-round-goal fixture), test_rounds_unknown_bucket_never_counts_as_first_pass (US1 sc.4), test_zero_denominator_reports_null_not_zero
- [X] T011 [US1] Doctor instance check: goal_convergence table exists, in devclaw/doctor/checks_instance.py, with a seeded-fault test in the matching tests module (spec 016 FR-014)

**Checkpoint**: US1 independently shippable — convergence truth exists even
before PR truth does

---

## Phase 4: User Story 2 — Ground-truth merge rate (Priority: P2)

**Goal**: distinct-PR counting; state from GitHub via the pr_ledger
refreshed once per cycle; bench split; loud staleness.

**Independent Test**: seed a ledger with 2 merged / 1 rejected / 1 open /
1 bench PR → opened=4, decided_merge_rate=2/3, bench segregated; refresh
with a FakeRemoteStates checker updates only undecided rows and stamps
as_of; an unreachable PR lands in unknown without failing.

- [ ] T012 [US2] Add `pr_ledger` table to devclaw/state_store/schema.py per data-model.md
- [ ] T013 [US2] Upsert `pr_ledger` rows (INSERT OR IGNORE, state='open') at the settle site that records eval_outcomes pr_url, plus `upsert_pr_states` + `undecided_pr_urls(window)` methods in devclaw/state_store/evals.py
- [ ] T014 [P] [US2] Add a `pr_state(pr_url)` lookup (gh pr view --json state,mergedAt → open|merged|rejected|unknown) on the existing `_gh` seam with an injectable checker, in devclaw/goal/remote_checks.py
- [ ] T015 [US2] Bounded ledger refresh inside `_maybe_emit_cycle_report` in devclaw/goal/service.py: undecided in-window rows only, hard cap (default 50) with a loud refresh_truncated flag persisted for the scorecard, checker injected the way default_checker() is bound today
- [ ] T016 [P] [US2] Add `Project.bench: bool = False` (dataclass + to_dict/from_dict + update_project passthrough) in devclaw/project_registry.py and the update_project tool surface in devclaw/server/tools/
- [ ] T017 [US2] Replace `merge_rate` with the `pr` block in devclaw/telemetry.py — distinct-PR counts, decided_merge_rate, unknown bucket, state_as_of_ms stamp, refresh_truncated, bench sub-block — and render it in devclaw/cli.py
- [ ] T018 [P] [US2] Named refresh tests in tests/test_pr_ledger_refresh.py: test_refresh_touches_only_undecided_in_window, test_refresh_cap_reports_truncation, test_unreachable_pr_lands_unknown_without_failing, test_reopened_pr_returns_to_open
- [ ] T019 [P] [US2] Named seeded-metric tests in tests/test_telemetry_scorecard.py: test_increments_sharing_goal_branch_pr_count_once (SC-001 18-not-36 fixture), test_review_tasks_move_no_pr_number, test_open_and_unknown_in_no_rate_denominator, test_bench_project_moves_only_bench_figures (SC-006)
- [ ] T020 [US2] Extend the zero-token idle guard in tests/test_goal_tick.py: test_idle_tick_makes_no_gh_calls_with_pr_ledger_wired (checker call count == 0 on idle paths; FakeClaude.calls == 0 untouched)
- [ ] T021 [US2] Doctor instance checks: pr_ledger table exists + ledger-staleness surfaced (never-refreshed reported, not failed) in devclaw/doctor/checks_instance.py, with seeded-fault tests

**Checkpoint**: US1 and US2 each independently shippable

---

## Phase 5: User Story 4 — The finish line, machine-checked (Priority: P2)

**Goal**: thresholds as config; per-metric pass/fail + one overall
autonomy-gate verdict; informational only.

**Independent Test**: seed metrics just above/below each threshold and a
non-clean cycle row → per-check pass/fail and the overall AND flip
accordingly; null values never pass.

**Depends on**: US1 + US2 (their corrected values are the inputs).

- [ ] T022 [P] [US4] Add DEVCLAW_RATCHET_FIRST_PASS / DEVCLAW_RATCHET_DECIDED_MERGE / DEVCLAW_RATCHET_WINDOW_DAYS through the single doorway in devclaw/config.py (one home, one default, one parse)
- [ ] T023 [US4] Add the `ratchet` block to devclaw/telemetry.py — thresholds echoed, per-metric checks, wedge_free_window from cycle_reports.clean over the window, overall AND, null-never-passes — and render the verdict line in devclaw/cli.py
- [ ] T024 [P] [US4] Named tests in tests/test_telemetry_scorecard.py: test_ratchet_flips_per_threshold_boundary, test_null_metric_never_passes_gate, test_nonclean_cycle_fails_wedge_free_check, test_thresholds_come_from_config_and_are_echoed
- [ ] T025 [US4] Assert nothing actuates from the verdict: a grep-style guard test that no goal/tick/dispatch module imports the ratchet result (tests/test_telemetry_scorecard.py::test_ratchet_is_informational_only)

**Checkpoint**: "are we finished?" is a single read

---

## Phase 6: User Story 3 — Steering split (Priority: P3)

**Goal**: human steers counted from stored sources; the conflated
steer_rate retired.

**Independent Test**: seed 3 owner + 5 auto-eval steering rows across two
goals → human_steers=3; no output field implies human steering computed from
machine verdicts.

- [ ] T026 [US3] Add the `steering` block (human_steers from goal_steering source NOT LIKE 'auto-%' over the shared devclaw.db connection; machine_correction_rounds_median aliasing convergence) and REMOVE `evaluator.steer_rate` + the two obsoleted estimate_notes in devclaw/telemetry.py; update devclaw/cli.py rendering
- [ ] T027 [P] [US3] Named tests in tests/test_telemetry_scorecard.py: test_human_steers_exclude_auto_sources, test_no_field_names_human_steering_from_machine_verdicts (wire-shape scan of the output keys)

**Checkpoint**: all four stories independently functional

---

## Phase 7: Polish & Cross-Cutting

- [ ] T028 Wire-shape contract test pinning the full corrected output against specs/018-scorecard-ratchet/contracts/scorecard-output.md (presence AND absence of the removed legacy fields) in tests/test_telemetry_scorecard.py
- [ ] T029 [P] Docs honesty: add the three DEVCLAW_RATCHET_* vars to docs/reference/env-vars.md, sweep docs mentioning merge_rate/first_pass_hit_rate/steer_rate, update the get_scorecard_metrics docstring in devclaw/server/tools/observability.py, and bump touched docs' currency tags in docs/INDEX.md
- [ ] T030 Run specs/018-scorecard-ratchet/quickstart.md end-to-end: full suite vs T001 baseline, ruff, mypy, live CLI read against a dev instance

---

## Dependencies & Execution Order

- **Phase 1 → 2**: sequential, quick.
- **US1 (Phase 3)** and **US2 (Phase 4)**: independent of each other after
  Phase 2; both touch schema.py and telemetry.py, so as ONE-PR-per-story
  increments they land sequentially (US1 first — it is the MVP), with US2
  rebasing trivially (additive table + additive block).
- **US4 (Phase 5)**: after US1 + US2 (consumes their values).
- **US3 (Phase 6)**: independent after Phase 2; sequenced last only because
  it is P3.
- **Polish (Phase 7)**: after all stories.

### Parallel Opportunities

Within each story, tasks marked [P] touch disjoint files (tests vs
production modules, registry vs remote_checks) and can be written
concurrently. Across stories, parallelism is deliberately NOT exploited —
one worker, one reviewable PR per story, per repo convention.

## Implementation Strategy

MVP = Phase 1–3 (US1): convergence truth ships alone and is already the
metric that is failing today. Then US2 → US4 → US3, one PR each, validating
each checkpoint's independent test before moving on. The whole spec is the
commitment: no story is left "SPECIFIED, NOT IMPLEMENTED" without saying so
out loud.

## Implementation notes (US1, 2026-08-25)

- The convergence ledger landed in the GOAL store schema
  (`devclaw/goal/state.py` + `state_status.py` + `store/status.py`), not
  `state_store/evals.py` as first drafted: GoalState owns the goal tables on
  the SAME shared devclaw.db, and the close/cancel sites hold the GoalStore —
  single-writer with zero new plumbing. Telemetry reads it over the shared
  connection.
- Rounds are counted from the append-only phase history's `verifying`
  entries at terminal-write time (`count_verifying_rounds`) instead of
  persisting a second counter: `donegate_rounds` is a STREAK counter that a
  human steer/resume legitimately resets, so it cannot carry lifetime rounds.
- T002's fixture helpers landed inline in `tests/test_telemetry_scorecard.py`
  (`_with_goal_tables` / `_seed_convergence`) — a separate module for two
  helpers used by one file was overhead.
- Bench-goal exclusion from convergence arrives with the `bench` flag in
  US2 (the flag does not exist yet, so there is nothing to exclude).
