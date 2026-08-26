# Tasks: Worker Context-Budget Invariant

**Input**: Design documents from `/specs/021-worker-context-budget/`

**Prerequisites**: plan.md, spec.md (clarified 2026-08-26), research.md, data-model.md, contracts/

**Tests**: INCLUDED — the repo convention (every behavior-change PR ships a
named regression test) makes them mandatory, not optional.

**Organization**: grouped by user story; each story lands as ONE coherent
reviewable PR (slice for reviewability; the whole spec is the commitment).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Create shared chunk-grammar fixtures (valid / edge / corrupt tasks.md samples) per contracts/chunk-grammar.md in tests/fixtures/chunk_grammar/ and a fixture-loading helper in tests/chunk_grammar_fixtures.py

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the ACP turn-sequencing primitive and the config knob that BOTH
US1 (watcher stop) and US2 (tripwire) build on; the fail-open hole fix.

- [ ] T002 Refactor runner/acp_client.py `AcpClient.run()` into a reusable prompt-turn primitive: support a follow-up `session/prompt` in the SAME session after a runner-initiated `session/cancel` (`cancel_turn()` + `prompt_again()`), and add a notification-observer hook the pump loop calls per `session/update` (research.md R2; no behavior change for the plain one-turn path)
- [ ] T003 Close the cancelled→ok hole: a `"cancelled"` stopReason outside a runner-initiated cancel sequence produces `status:"error"` (fail closed) in runner/runner.py; add fake-agent script `script_cancelled` to tests/acp_fake_agent.py and named regression test `test_cancelled_stop_reason_fails_closed_not_ok` in tests/test_runner_acp.py
- [ ] T004 [P] Add `context_tripwire_pct()` accessor (default 75, 0 disables) to devclaw/config.py per the call-time pattern, plus the row in docs/reference/env-vars.md (doc-sync test `test_env_vars_doc_sync` must pass)
- [ ] T005 [P] Forward `DEVCLAW_CONTEXT_TRIPWIRE_PCT` into the sandbox env in devclaw/engine/sandcastle.py and devclaw/engine/host.py (same allowlist loop as `DEVCLAW_SANDBOX_MEMORY`/`_CPUS`) with a named test in tests/test_acp_command_config.py or sibling

**Checkpoint**: turn sequencing + knob exist; hole closed; suite green.

## Phase 3: User Story 1 — chunked delivery, harness-enforced (P1) 🎯 MVP

**Goal**: one session = one story-slice, mechanically; continuation from
workspace + settle records; bounded briefs; loud block on corrupt artifact.

**Independent Test**: fake-agent subprocess run where the agent flips a full
`[US1]` slice then touches `[US2]` → runner stops the turn, result carries
`chunk.stopped_by_watcher=true`; continuation brief stays bounded; corrupt
tasks.md mid-arc blocks loud with zero LLM calls.

- [ ] T006 [US1] Implement the runner-side chunk-grammar mini parser (~30 lines, stdlib) in runner/runner.py, tested against the shared fixtures in a new tests/test_chunk_grammar.py that ALSO runs devclaw/goal/slice_guard.py's parser over the same fixtures (anti-drift, contracts/chunk-grammar.md)
- [ ] T007 [US1] Implement the slice watcher in runner/runner.py: session-start snapshot of specs/*/tasks.md, mtime-gated re-read on `tool_call_update`, stop condition = (≥1 slice advanced) AND (a write touches a task row outside every advanced slice); disarmed when no specs/ tree or ≤1 incomplete slice at start (FR-005)
- [ ] T008 [US1] Wire watcher-stop to the T002 sequence: cancel turn → "slice complete — commit, update tasks.md honestly, stop" follow-up prompt → normal verify/materialize/result; populate result fields `chunk{feature, advanced_slices, stopped_by_watcher}` per contracts/runner-result.md in runner/runner.py
- [ ] T009 [US1] Add fake-agent scripts `script_slice_flip` and `script_landing` to tests/acp_fake_agent.py; subprocess tests in tests/test_runner_acp.py: `test_watcher_stops_session_after_one_slice`, `test_single_slice_workspace_never_arms_watcher`, `test_worker_wrapup_does_not_trigger_stop`
- [ ] T010 [US1] Runner reports `blocked` with a corrupt-artifact reason when a continuation workspace has an unparseable tasks.md (contracts/chunk-grammar.md); host renders the existing loud-block path; named tests: runner-side in tests/test_runner_blocked.py, host-side `test_corrupt_chunk_plan_blocks_loud_with_zero_llm_calls` in tests/test_goal_tick.py (FakeClaude.calls == 0)
- [ ] T011 [US1] Update runner/skills/_writes-code/05-speckit-memory.md: state the chunk contract once, imperatively (one slice per session; the harness enforces the stop; per-slice honesty in tasks.md); bump the brief-size ceilings in tests/test_runner_skills.py deliberately if needed; presence AND absence assertions in tests/test_worker_skill_content.py
- [ ] T012 [US1] Continuation brief framing in devclaw/goal/tick.py `_advance_brief`: name the current feature dir + "smallest incomplete slice" continuation explicitly; named test `test_continuation_brief_is_bounded_regardless_of_prior_chunk_count` in tests/test_thin_plan_advance.py asserting FakeEngine.dispatched brief size is flat as increments grow (FR-003; prompt_budget caps hold)
- [ ] T013 [US1] Oversized-slice mark: devclaw/queue/settle.py stamps the active slice (from result `chunk`/`tripwire` when present) into the `_PROMPT_TOO_LONG_MARKER` failure detail; `_advance_brief` failure-context branch renders "re-slice T00x/USn in tasks.md before implementing"; named test `test_oversized_chunk_demands_reslice_and_refuses_identical_retry` in tests/test_task_retry.py (FR-008)

**Checkpoint**: US1 independently shippable — one PR, suite green, ruff+mypy clean.

## Phase 4: User Story 2 — context tripwire lands instead of crashing (P2)

**Goal**: sessions near the ceiling land a verified partial increment; every
firing observable; absent signal inert-but-loud.

**Independent Test**: fake agent streams rising `usage_update` past the
threshold → exactly one ContextTripwire event, cancel + land-now on the fake
agent transcript, result `tripwire.landed=true`, one problems-catalog row.

- [ ] T014 [US2] Parse `sessionUpdate == "usage_update"` (`used`/`size`) in runner/acp_client.py via the T002 observer hook, tracking the latest ratio; tolerant of unknown shapes; existing additive usage accumulation untouched; unit tests in tests/test_acp_client.py with a new `script_usage_window` in tests/acp_fake_agent.py
- [ ] T015 [US2] Tripwire orchestration in runner/runner.py: threshold from `DEVCLAW_CONTEXT_TRIPWIRE_PCT` (runner reads own env), fire at most once → cancel + land-now follow-up ("finish the current coherent piece: commit, update tasks.md honestly, stop") → result `tripwire{threshold_pct, used, size, active_slice, landed}` + `context{used,size}` + `ContextTripwire` event line; `usage_absent_note` when configured >0 but no stream (FR-007) per contracts/runner-result.md
- [ ] T016 [US2] Subprocess tests in tests/test_runner_acp.py with `script_usage_window` + `script_overrun_landing`: `test_tripwire_fires_once_and_lands_a_normal_delivery`, `test_below_threshold_behavior_is_byte_identical`, `test_absent_usage_stream_is_inert_and_loud`, `test_tripwire_disabled_at_zero`
- [ ] T017 [US2] Host settle: `tripwire` result field → `StateStore.record_problem(category="limit", kind="context_tripwire", recovered=<landed>)` in devclaw/queue/settle.py; friendly `ContextTripwire` case in devclaw/server/worker_events.py `_classify`; named test `test_tripwire_firing_lands_one_problems_row_countable_per_goal` in tests/test_goal_store_problems.py or sibling (SC-005 readable via list_problems)

**Checkpoint**: US2 independently shippable — one PR.

## Phase 5: User Story 3 — read-side diet (P3)

**Goal**: exploration cost paid once in planning; build sessions pull
distilled per-slice context + repo brief before raw exploration.

**Independent Test**: skill-content tests assert the pull-order instruction
(plan.md slice entry + repo brief FIRST, raw reads bounded to the slice's
declared surface, stale entries refreshed explicitly) is present exactly once
and PLAN.md prohibitions remain.

- [ ] T018 [US3] Extend runner/skills/_writes-code/05-speckit-memory.md: during planning, record per-slice distilled context (files/areas touched, constraints learned) in the feature's plan.md; when implementing a slice, read its plan.md entry + the repo brief FIRST and explore raw files only within the slice's declared surface; refresh stale entries explicitly (state each rule once; no war stories)
- [ ] T019 [P] [US3] Verify/extend the repo-brief pointer in runner/skills/_common.md pull-order (only if absent); presence AND absence assertions in tests/test_worker_skill_content.py + tests/test_speckit_memory_skill.py; ceiling bumps in tests/test_runner_skills.py deliberate and named in the PR body

**Checkpoint**: US3 shippable — one (small) PR, possibly folded with Polish.

## Phase 6: Polish & Cross-Cutting

- [ ] T020 [P] Docs honesty: update docs/architecture.md (runner seam: watcher + tripwire), docs/flows/task-execution.md (the tripwire/watcher hops), docs/reference/env-vars.md already done in T004; refresh currency tags in docs/INDEX.md
- [ ] T021 Re-check spec 016 FR-014: confirm no persisted-state shape change landed (expected: none — markers live in existing detail strings/result_json); state the conclusion in the final PR body, or add the doctor check + seeded-fault test if the implementation drifted
- [ ] T022 Run quickstart.md validation end-to-end: full suite ≥ baseline count, `ruff check .` clean, `mypy` clean; record the green baseline in the PR description

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 (Phase 3) → US2 (Phase 4) → US3 (Phase 5) → Polish.
- US2 depends on Phase 2 (T002 sequence, T004/T005 knob) and reuses U S1's
  active-slice reporting (T008) for `tripwire.active_slice`, but is
  independently testable (the field is nullable).
- US3 depends only on US1's skill file existing in its final shape (same
  file, sequential edits — not parallel with T011).
- Within US1: T006 → T007 → T008 → T009; T010–T013 after T008; T011 parallel
  with T012/T013 (different files).

## Parallel Opportunities

- T004 ∥ T005 (different files) after T003.
- T011 ∥ T012 ∥ T013 within US1 (skills vs tick.py vs settle.py).
- T019 ∥ T020 in the tail.

## Implementation Strategy

- **PR 1 (MVP)**: Phases 1+2+US1 — the class-fix core: enforced chunking,
  bounded continuation, loud corrupt-artifact block, oversized-slice re-slice.
- **PR 2**: US2 — tripwire + observability (the SC-005 instrument).
- **PR 3**: US3 + Polish — skill diet + docs.
- Each PR: full suite ≥ baseline, ruff+mypy clean, named regression tests
  listed in the body, branch-per-change in a worktree, squash merge.
- Deploy note: runner/skills changes require a sandbox-image rebuild at the
  next VPS deploy (same coupling spec 020 US2 already queued).
