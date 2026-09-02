# Tasks: Structured problem resolution

**Input**: Design documents from `/specs/031-problem-resolution/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tripwire-class only (ruled 2026-08-29) — every test task below extends an
existing class test named in quickstart.md or adds a doctor seeded-fault pair.
Ordinary rendering/wording ships no test; the live walkthrough in quickstart.md
is its regression surface.

**Organization**: Three increments, each ONE reviewable PR. Phase 3 is US1+US2
together (a Problem nobody can answer in a typed way is the current failure with
better formatting). Phase 4 is US4 *before* Phase 5 / US3, because a lint
rewrite is itself a Decision that must reach the gate.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: different files, no dependency on an incomplete task
- **[Story]**: US1 · US2 · US3 · US4 (spec.md); Setup/Foundational/Polish carry none
- Paths are repository-relative; the layer map in plan.md decides where a change lives

## Path Conventions

Single package: `devclaw/` (host), `runner/` (in-sandbox), `tests/` (stubbed
tripwires), `docs/`, `specs/`. New modules are named in plan.md's source tree.

---

## Phase 1: Setup

**Purpose**: nothing to scaffold — the feature lands inside the existing package. One task pins the baseline so every later PR compares against it.

- [ ] T001 Record the green baseline (suite count, ruff, mypy) at branch point `4dfa0b9` in specs/031-problem-resolution/tasks.md under Notes, and confirm `.specify/feature.json` points at specs/031-problem-resolution

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the persisted shape and the read/write seams every story consumes. Ships INSIDE the P1 PR (Phase 3), never alone — a schema with no raiser is drift.

**⚠️ CRITICAL**: Phase 3 tasks depend on all of Phase 2.

- [ ] T002 [P] Add frozen dataclasses `ProblemOption`, `Problem`, `Decision` and `GoalStatus.problem_id: str = ""`; add `ClauseVerdict.resolved_by: str = ""` — in devclaw/goal/models.py (fields per data-model.md)
- [ ] T003 [P] Add `CREATE TABLE IF NOT EXISTS goal_problems` and `goal_decisions` (columns + indexes per data-model.md) and the idempotent `ALTER TABLE goal_status ADD COLUMN problem_id TEXT NOT NULL DEFAULT ''` beside `donegate_progress` — in devclaw/goal/state.py
- [ ] T004 Persist/read `problem_id` at the four `goal_status` sites (INSERT column list, VALUES placeholder, UPSERT SET, row → dataclass) — in devclaw/goal/state_status.py (depends on T002, T003)
- [ ] T005 Create devclaw/goal/state_problems.py — row I/O: `insert_problem`, `close_problem(id, status, decision_id)`, `supersede_open_problems(goal_id)`, `current_problem(goal_id)`, `insert_decision`, `supersede_decisions(goal_id, clause, by_id)`, `current_decisions(goal_id)`; all take the open connection so callers run them inside `transaction()` (depends on T003)
- [ ] T006 Expose store methods `raise_problem(...)`, `record_decision(...)`, `current_problem(goal_id)`, `decisions(goal_id)` on the content mixin, delegating to state_problems — in devclaw/goal/store/content.py (depends on T005); document that `raise_problem` supersedes any open Problem and `record_decision` closes the Problem and supersedes an earlier Decision on the same clause
- [ ] T007 [P] Add `check_problems_tables` (`instance.problems.tables`) and `check_problem_status_pointer` (`instance.problems.status_pointer`: `goal_status.problem_id` present AND no row points at a non-`open` Problem) mirroring `check_goal_status_slice_hold_count`; register both in `INSTANCE_CHECKS` — in devclaw/doctor/checks_instance.py (depends on T003)
- [ ] T008 [P] Seeded-fault pairs `test_problems_tables_absent_detected` / `_present_is_ok` (DROP TABLE goal_problems) and `test_problem_pointer_drift_detected` / `_healthy_is_ok` (point a row's `problem_id` at a `resolved` Problem) mirroring the slice_hold_count pair — in tests/test_doctor.py (depends on T007)

**Checkpoint**: schema + store seams exist; nothing raises a Problem yet; suite, ruff, mypy green; doctor pair green.

---

## Phase 3: User Story 1 + User Story 2 — a typed Problem, resolved by one of two moves (Priority: P1) 🎯 MVP

**Goal**: every human-gated block carries a Problem; the owner resolves it with `correct_implementation` or `decide`; a timed-out Problem takes its default and informs; `steer_goal` is refused while a Problem is open. One PR.

**Independent Test**: seed a goal, drive the done-gate to `needs_human`; assert the block carries a six-field Problem, the ping names clause + options + the two verbs; call each verb over MCP; assert the goal unblocks, a Decision row exists, no steering line was written; advance the clock past the timebox on a fresh Problem; assert a `defaulted` Decision and one ℹ️ ping.

### Tripwire tests for Phase 3 (extend existing class tests; verify each FAILS before its implementation task lands)

- [ ] T009 [P] [US2] `test_blocked_goal_with_open_problem_costs_zero_cognition` — extend the idle-guard family: blocked goal with `problem_id` set, timebox not elapsed, tick → `FakeClaude.calls == 0`, no dispatch — in tests/test_goal_tick.py
- [ ] T010 [P] [US2] `test_timebox_default_applies_without_cognition` — same family: timebox elapsed → Decision `provenance=defaulted`, goal idle, `FakeClaude.calls == 0`, exactly one notifier line containing "defaulted" — in tests/test_goal_tick.py
- [ ] T011 [P] [US2] `test_resolve_problem_is_one_transaction_with_unblock` — inject a `TransitionConflict` on the UNBLOCK; assert no Decision row and the Problem still `open` — in tests/test_goal_transactional.py
- [ ] T012 [P] [US2] `test_unblock_by_resolution_rides_legal_unchanged` — extend the LEGAL-table structural test: `Event.UNBLOCK` from `BLOCKED` is the resolution edge; no new `State`/`Event` symbols exist — in tests/test_goal_transitions.py
- [ ] T013 [P] [US2] `test_defaulted_accept_and_close_never_emits_achieve` — extend `test_green_mechanical_verification_alone_never_closes_a_goal`'s family: a defaulted `accept_close` under `trust` lands `idle` (never `done`); under `strict` the goal stays `blocked`, no Decision row, one notice — in tests/test_goal_tick.py
- [ ] T014 [P] [US2] `test_steer_goal_is_refused_while_a_problem_is_open` — `steer_goal` raises, message names the Problem id and both verbs, `goal_steering` has no new row, status version unchanged — in tests/test_goal_tick.py
- [ ] T015 [P] [US1] `test_worker_honest_block_raises_a_problem_without_burning_the_cap` — a failed settle whose detail carries the worker-blocked marker → `blocked`, Problem `raised_by=worker_block` with the fixed option set, `actions_dispatched` unchanged — in tests/test_goal_tick.py

### Implementation for Phase 3

- [ ] T016 [US1] Create devclaw/goal/problems.py — the ONE raise seam `raise_problem(store, goal_id, *, kind, raised_by, what, clause, why, options, default_key, timebox_s=DEFAULT_TIMEBOX_S)`; option-set builders `options_from_corrections(corrections)` (c1…cN + fixed tail `accept_close`, `split`), `CHURN_OPTIONS`, `WORKER_BLOCK_OPTIONS`; `summary_line(problem)` for `blocked_on`; `DEFAULT_TIMEBOX_S = 12*3600` read via devclaw/config.py doorway `DEVCLAW_PROBLEM_TIMEBOX_S` (document in docs/reference/env-vars.md) (depends on T006)
- [ ] T017 [US1] Raise a Problem in the done-gate `needs_human` branch (kind `needs_answer`, `raised_by=done_gate`, options from corrections, default = first correction else `accept_close`) and in the churn park (kind `donegate_churn`, `raised_by=churn_park`, `CHURN_OPTIONS`, default `correct`) — inside the existing BLOCK transitions, setting `problem_id` and `blocked_on=summary_line(...)` — in devclaw/goal/tick_donegate.py (depends on T016)
- [ ] T018 [US1] Raise a Problem for a worker honest-block at settle: when a failed poll's detail carries the worker-blocked marker, BLOCK immediately (kind `needs_answer`, `raised_by=worker_block`, `WORKER_BLOCK_OPTIONS`, default `correct`) instead of counting toward the cap — in devclaw/goal/tick_settle.py (depends on T016); keep the existing `mechanical_setup` routing above it untouched
- [ ] T019 [US1] Raise a Problem at the dispatch-time `needs_answer` park (the re-slice park) with the fixed churn set and `raised_by=dispatch_park` — in devclaw/goal/tick.py (depends on T016)
- [ ] T020 [US2] Add `GoalService.resolve_problem(goal_id, problem_id, *, verb, option=None, text=None, made_by="denys")`: validate (current open Problem, verb/args per contracts/mcp-and-http.md), then in ONE `transaction()`: `record_decision(provenance=owner)` + `close_problem(resolved)` + `Event.UNBLOCK` with `steer_goal`'s exact reset shape plus `problem_id=""`; `poke()`; return the contract response — in devclaw/goal/service.py (depends on T006)
- [ ] T021 [US2] Refuse `steer_goal` when `status.problem_id` is set: raise `ValueError` carrying the rendered Problem + "resolve it with correct_implementation or decide"; write nothing — in devclaw/goal/service.py (depends on T020)
- [ ] T022 [US2] Timebox default on the tick's blocked branch, BEFORE the `should_plan` gate: if `problem_id` set and `now >= timebox_at` → if default option `closes_goal` and strictness is `strict`: notify once ("only an explicit decide can close it") and stay blocked; else `record_decision(provenance=defaulted, made_by=tick)` + close Problem `defaulted` + UNBLOCK, one ℹ️ notice — in devclaw/goal/tick.py (depends on T016, T006); no cognition on this path
- [ ] T023 [US2] Make `cancel_goal` mark the open Problem `superseded` in its transaction — in devclaw/goal/service.py (depends on T006)
- [ ] T024 [P] [US2] Add MCP tools `correct_implementation(goal_id, problem_id, correction)` and `decide(goal_id, problem_id, option=None, text=None)`; convert service `ValueError`s to `ToolError`; make `steer_goal` surface the refusal as `ToolError` — in devclaw/server/tools/goals.py (depends on T020, T021)
- [ ] T025 [P] [US2] Add `POST /goals/{goal_id}/resolve` (body per contract; 400 codes `invalid_verb|stale_problem|bad_option|missing_field` with the current Problem); make `POST /goals/{id}/steer` return 409 `problem_open` when refused — in devclaw/server/routes/goals.py (depends on T020, T021)
- [ ] T026 [P] [US1] Carry `problem` (full object) and `decisions` (current) in `get_goal` / `list_goals` (service projections) and in `goal_json` — in devclaw/goal/service.py and devclaw/server/routes/goals.py (depends on T006)
- [ ] T027 [US1] Owner-ping wording per contracts/mcp-and-http.md (clause, options with default marked, timebox, the two verbs; never `steer_goal`) at the three raise sites and the defaulted notice — in devclaw/goal/tick_donegate.py, devclaw/goal/tick_settle.py, devclaw/goal/tick.py (depends on T017–T019, T022)
- [ ] T028 [US1] Console: render the Problem (fields + options + default + countdown) and the current Decisions on the goal page; wire the two verbs to `/resolve`; show the 409 on steer — in console/ (the goal view components) (depends on T025, T026)
- [ ] T029 [US2] Add a devclaw/goal/problems.py docstring + module comment in devclaw/goal/tick.py naming the zero-token property of the timebox check; verify `FakeClaude.calls == 0` family is green (depends on T022)

**Checkpoint (end of P1 PR)**: T009–T015 green (each verified red-before-green), full suite + ruff + mypy green, doctor pair green; live walkthrough steps 3–5, 7, 8 of quickstart.md pass on the deployed instance.

---

## Phase 4: User Story 4 — Decisions fed forward as fact (Priority: P2a)

**Goal**: every current Decision reaches the next brief and the done-gate through the prior-increments channel; the gate grades a decided clause `resolved_by_decision` and never re-litigates it. One PR.

**Independent Test**: record a Decision on a clause; dispatch; assert the brief carries the Decisions section under its marker; run the done-gate with a stub verdict citing `resolved_by`; assert the clause counts as satisfied and the aggregate verdict honours it.

### Tripwire tests for Phase 4

- [ ] T030 [P] [US4] `test_decisions_marker_present_and_capped` — beside the prior-increments structural guard: head line is `DECISIONS_MARKER`; superseded Decisions absent; entry list capped under `DECISIONS_KEEP` with the marker never truncated — in tests/test_goal_tick.py
- [ ] T031 [P] [US4] Extend the evaluator omission/presence prompt test: with `decisions=None` the rendered prompt contains no `DECISIONS` block (prove absence from the raw template first); with a section it does — in the existing evaluator prompt test module (tests/cognition/ or tests/test_goal_tick.py where the #234 pattern lives)

### Implementation for Phase 4

- [ ] T032 [P] [US4] Create devclaw/goal/decisions.py — `render(rows) -> str` per contracts/brief-section.md (marker head line, one line per current Decision, devclaw-controlled fields only) (depends on T006)
- [ ] T033 [P] [US4] Add `DECISIONS_MARKER = "Decisions on this goal"` and `DECISIONS_KEEP` + `cap_decisions()` beside the prior-increments cap — in devclaw/advance_brief.py and devclaw/goal/prompt_budget.py
- [ ] T034 [US4] Slot the rendered section into the advance brief directly after prior increments; read `store.decisions(goal_id)` BELOW the `should_plan` gate — in devclaw/goal/tick.py (depends on T032, T033)
- [ ] T035 [US4] Add blank-safe `decisions: str | None = None` to `evaluate(...)`; render a `DECISIONS` grounding block when non-blank; parse optional `resolved_by` on clause objects into `ClauseVerdict.resolved_by` and count such clauses as satisfied; ignore malformed values — in devclaw/goal/evaluator.py (depends on T002)
- [ ] T036 [US4] Add the one-sentence rule (a clause with a current Decision is graded `resolved_by_decision`, cited as evidence, not re-evaluated) in the procedure section; reference the block as *Decisions* in prose, never the literal header — in devclaw/prompts/goal-evaluator.md (depends on T035)
- [ ] T037 [US4] Collect `decisions.render(store.decisions(goal_id))` at the done-gate call site and pass it through — in devclaw/goal/tick_donegate.py (depends on T032, T035)
- [ ] T038 [P] [US4] Add the single worker-skill line ("A *Decisions on this goal* section in your brief is settled fact from the owner: apply it, never re-open it, cite it in your hand-back") — in runner/skills/_writes-code/05-speckit-memory.md; confirm the always-on brief stays under the leanness guard (tests/test_runner_skills.py)

**Checkpoint (end of P2a PR)**: T030–T031 green; suite + ruff + mypy green; live step 6 of quickstart.md passes.

---

## Phase 5: User Story 3 — defective contracts refused at authoring (Priority: P2b)

**Goal**: at creation, a `done_when` clause needing a sandbox-impossible capability is refused; a baseline-less absolute predicate is rewritten and recorded as an admission Decision; an undecided design choice becomes a Problem to the author before any dispatch. One PR.

**Independent Test**: submit each defective clause shape via `create_goal`; assert refusal / rewrite+Decision / admission Problem respectively; submit the corrected forms; assert unchanged admission. Replay the four 2026-09-02 contracts (SC-004).

### Tripwire tests for Phase 5

- [ ] T039 [P] [US3] `test_admission_lint_catches_the_three_classes` — parametrized over class (a) refuse / (b) rewrite / (c) problem, plus the four 2026-09-02 contracts and their corrected forms; asserts nothing is persisted on refusal — in tests/test_goal_tick.py (beside the spec-019 readiness refusal tests)
- [ ] T040 [P] [US3] Extend the zero-token family: the lint's class-(c) cognition call happens at creation only — a tick over an admitted goal makes zero calls — in tests/test_goal_tick.py

### Implementation for Phase 5

- [ ] T041 [US3] Create devclaw/goal/admission_lint.py — `lint(done_when, *, workspace_dir) -> LintResult(refusals, rewrites, problems)`: class (a) mechanical vocabulary + the manifest's undeclared-capability complement; class (b) mechanical rewrite to "no new failures relative to the default branch"; class (c) via the existing intake-readiness cognition caller, returning a Problem payload (kind `admission`, `raised_by=admission_lint`, options from the reading, default = the first) (depends on T016)
- [ ] T042 [US3] Call the lint in `create_goal_async` after the referenced-contract readiness check and before anything persists: refusal → `ValueError` per contract, nothing persisted; rewrites → apply to the contract, `record_decision(provenance=admission, made_by=admission_lint)` after the goal row exists; class (c) → create the goal `blocked` with the admission Problem; return `admission` in the response — in devclaw/goal/service.py (depends on T041, T006)
- [ ] T043 [P] [US3] Surface the refusal as `ToolError` and the `admission` block in `create_goal`'s response — in devclaw/server/tools/goals.py (depends on T042)
- [ ] T044 [US3] Record a lint miss: when a worker honest-block names a capability the lint should have caught, also `record_problem(category="admission", kind="lint_miss")` in the problems catalog — in devclaw/goal/tick_settle.py (depends on T018, T041)

**Checkpoint (end of P2b PR)**: T039–T040 green; suite + ruff + mypy green; live steps 1–2 of quickstart.md pass; SC-004 replay recorded in the PR body.

---

## Phase 6: Polish & cross-cutting

**Purpose**: docs honesty and the metric — each in the PR that makes the doc stale.

- [ ] T045 [P] CLAUDE.md: replace the "steer_goal … answer what a goal is blocked on" wording with the two verbs; add the Problem/Decision invariant line under "Mechanical blocks auto-heal; recovery is a verb" (P1 PR)
- [ ] T046 [P] docs/architecture.md: the human-gated block section describes a typed Problem and the two resolution verbs; the churn-brake paragraph gains "…raises a Problem" (P1 PR); docs/INDEX.md currency tag
- [ ] T047 [P] docs/flows/delivery.md and docs/flows/task-execution.md: the settle/park hops name `raise_problem` and the resolve route (P1 PR); docs/INDEX.md currency tags
- [ ] T048 [P] docs/reference/env-vars.md: `DEVCLAW_PROBLEM_TIMEBOX_S` row (P1 PR) — the env-doc parity guard enforces it
- [ ] T049 [P] Add `evals/ping_profile.py` — pings per goal-week and the provenance split over `goal_problems`/`goal_decisions` (SC-001/002), documented in evals/README.md (P1 PR; extend after P2a/P2b)
- [ ] T050 Run the full quickstart.md live walkthrough against the deployed instance after each increment's deploy; record outcomes in the PR body (each PR)
- [ ] T051 Update `.specify/memory/constitution.md`? — NO: no invariant changes; note in the P1 PR body that the constitution check passed unchanged

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → **Phase 3 (US1+US2, P1 PR)** — Phase 2 ships inside the P1 PR
- **Phase 4 (US4, P2a PR)** depends on Phase 3 (needs `store.decisions`, `ClauseVerdict.resolved_by`)
- **Phase 5 (US3, P2b PR)** depends on Phase 4 (a lint rewrite is a Decision that must be fed forward) and on T016/T018 from Phase 3
- **Phase 6** tasks ride the PR named on each

### Within Phase 3

- T002 ∥ T003 → T004 → T005 → T006 → {T007 → T008} ∥ T016
- T016 → T017 ∥ T018 ∥ T019
- T006 → T020 → T021 → T024 ∥ T025 ; T006 → T026 ; T022 needs T016 + T006 ; T023 needs T006
- T027 after T017–T019 + T022 ; T028 after T025 + T026 ; T029 after T022
- Tests T009–T015 written first, each red until its implementation task

### Parallel Opportunities

- Phase 2: T002 ∥ T003; T007 ∥ T016 once T006 lands
- Phase 3 tests: T009–T015 all in parallel (distinct test functions, three files)
- Phase 3 impl: T017 ∥ T018 ∥ T019 (three files); T024 ∥ T025 ∥ T026 (three files)
- Phase 4: T030 ∥ T031; T032 ∥ T033; T038 independent
- Phase 5: T039 ∥ T040; T043 after T042
- Phase 6: T045–T049 all parallel within the P1 PR

---

## Parallel Example: Phase 3

```bash
# Tripwires first (all parallel — different functions/files):
Task: "test_blocked_goal_with_open_problem_costs_zero_cognition in tests/test_goal_tick.py"
Task: "test_resolve_problem_is_one_transaction_with_unblock in tests/test_goal_transactional.py"
Task: "test_unblock_by_resolution_rides_legal_unchanged in tests/test_goal_transitions.py"
# Raise sites (parallel — three files) once problems.py exists:
Task: "done-gate + churn park raise in devclaw/goal/tick_donegate.py"
Task: "worker honest-block raise in devclaw/goal/tick_settle.py"
Task: "dispatch-time park raise in devclaw/goal/tick.py"
# Surfaces (parallel) once the service verbs exist:
Task: "MCP tools in devclaw/server/tools/goals.py"
Task: "HTTP resolve route in devclaw/server/routes/goals.py"
Task: "get_goal/list_goals/goal_json projections"
```

---

## Implementation Strategy

### MVP = the P1 PR (Phases 1–3 + the Phase 6 docs tasks marked P1)

1. Land the schema + seams (Phase 2) — inert until a raiser exists
2. Write the seven tripwires red
3. One raise seam, four sites; the two verbs; the timebox; the refusal
4. **STOP and VALIDATE**: suite/ruff/mypy, doctor pair, live steps 3–5, 7, 8
5. Deploy; watch one real Problem get raised and resolved with `decide`

### Incremental delivery

1. P1 PR → the flow exists; steering is no longer the answer to a problem
2. P2a PR (US4) → decisions reach the worker and the gate; nothing re-asks
3. P2b PR (US3) → most problems never reach the owner
4. Two weeks on: `evals/ping_profile.py` against the 8-pings/day baseline decides whether the spec worked (SC-001/002); the whole spec is the commitment — P1 landing is not a stopping point

---

## Notes

- Baseline at branch point `4dfa0b9`: **1296 passed, 5 skipped**; ruff clean; mypy clean (140 files) — recorded per T001
- Never mint a sibling test: every tripwire above names the class test it extends
- The single-ACHIEVE-emitter structural guard pins the ACHIEVE line's literal text; if T022/T035 change nothing on that line, it stays untouched — verify before assuming
- `.specify/feature.json` → `specs/031-problem-resolution` (worktree `spec/031-problem-resolution`)
