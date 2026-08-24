# Tasks: Error-Issue Schema & Single Filing Doorway

**Input**: Design documents from `/specs/014-issue-doorway/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/issue-schema.md

**Tests**: Included — the repo's named-regression-test rule makes them mandatory
(every behavior-change PR ships one; SC-001..SC-004 each name an assertion).

**Organization**: US1+US2 land together as PR1 (dedup is core to the doorway,
not an add-on); US3 lands as PR2. Two reviewable increments; the whole spec is
the commitment.

> **Implementation note (2026-08-24)**: US3 came out at ~60 diff lines
> (`self_issue.py` inherits the doorway's creator + one rewired branch), so the
> whole spec shipped as ONE coherent PR — a stacked second PR would have been
> ceremony, not review value. Fingerprints turned out to be free-form at the
> producer (the catalog's carry spaces), so the metadata line percent-encodes
> them — research.md D2 amended by the contract file.

## Phase 1: Setup

*(no scaffolding needed — existing package, stdlib only; no tasks)*

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T001 Add `machine_issues` table to the state-store schema in `devclaw/state_store/schema.py` per data-model.md (PRIMARY KEY (repo, fingerprint); CREATE IF NOT EXISTS rides the existing bootstrap path)
- [X] T002 Create ledger mixin `devclaw/state_store/machine_issues.py` with `machine_issue_get(repo, fingerprint)`, `machine_issue_record(...)` (insert-or-occurrence-bump), `machine_issue_set_state(repo, fingerprint, state)`; wire the mixin into `StateStore` in `devclaw/state_store/core.py`
- [X] T003 Named store tests in `tests/test_issue_doorway.py`: record→get round-trip, occurrence bump increments count and refreshes `last_seen_ms`, state flip persists (use the existing tmp-path store fixture style from `tests/test_goal_store*.py`)

**Checkpoint**: ledger exists behind the single writer — doorway work can begin

## Phase 3: User Story 1 — machine-found problem becomes a predictable issue (P1) 🎯 MVP

**Goal**: `devclaw/issue_doorway.py` renders + files one schema-v1 issue per finding; failure is loud.

**Independent Test**: trigger one filing from a stubbed source; assert the created body parses against the schema with every mandatory field (US1 independent test).

- [X] T004 [US1] Create `MachineFinding` frozen dataclass in `devclaw/issue_doorway.py` with all-fields validation raising one `ValueError` naming every problem (fields + rules per data-model.md; severity whitelist; `proposed_done_when` ≥ 20 chars; fingerprint/source whitespace-free)
- [X] T005 [US1] Implement `render_issue_title(finding)` + `render_issue_body(finding)` in `devclaw/issue_doorway.py` per contracts/issue-schema.md (metadata comment line with v1+fingerprint+source+severity; canonical `##` sections; literal `unknown` for absent-but-stated; evidence truncation at 6,000 chars with explicit marker)
- [X] T006 [US1] Implement `parse_machine_issue(body)` in `devclaw/issue_doorway.py` — the contract regex + section extraction, version-dispatching, returning a `MachineFinding` + schema version (the round-trip proof for SC-001)
- [X] T007 [US1] Define `GhAdapter` protocol + `GhCli` (create/comment/reopen via `devclaw.procutil.run` shelling `gh`, fail-loud-not-fatal, `ensure_label` for `devclaw:machine-filed`) and `FilingOutcome` dataclass in `devclaw/issue_doorway.py`, mirroring the `goal/self_issue.py` adapter shape
- [X] T008 [US1] Implement `file_finding(gh, store, repo, finding, *, labels=(), now_ms)` new-issue path in `devclaw/issue_doorway.py`: no ledger row → create issue with marker label + pass-through labels → record ledger row → `FilingOutcome(action="filed")`; on gh failure → `FilingOutcome(action="failed", reason=…)` + a problems-catalog row via `store.record_problem` (kind `issue_filing_failed`) — never a silent drop
- [X] T009 [US1] Named tests in `tests/test_issue_doorway.py`: render→parse round-trip field-identical (SC-001); canonical section order asserted; `unknown` rendering; truncation marker with exact omitted-char count; invalid severity / short done-when rejected with all problems named; fake-gh failure yields failed outcome + problems row (US1 scenario 3); version line matches the contract regex
- [X] T010 [US1] Named test in `tests/test_issue_doorway.py`: a rendered doorway body runs through the intake grading parse path (`devclaw/intake.py` regrade parsing, stubbed cognition) without error — FR-008/SC-003 stub half

**Checkpoint**: one filing from a stubbed source produces a schema-v1 issue; failures are loud

## Phase 4: User Story 2 — filing is idempotent by fingerprint (P2)

**Goal**: same fingerprint never duplicates; closed+recurred reopens as a marked regression.

**Independent Test**: file the same fingerprint twice; assert one issue with an incremented occurrence record.

- [X] T011 [US2] Extend `file_finding` in `devclaw/issue_doorway.py` with the open-fingerprint path: ledger row `open` → occurrence comment per contract (occurrence n + fresh truncated evidence) → bump ledger → `FilingOutcome(action="updated")`
- [X] T012 [US2] Extend `file_finding` in `devclaw/issue_doorway.py` with the closed-fingerprint path: reopen + `**Recurrence** (regression)` comment per contract → ledger back to `open` → `FilingOutcome(action="reopened")`; gh reopen on an already-open issue is a tolerated no-op (ledger reconciles from the actual result, per data-model.md drift rule)
- [X] T013 [US2] Named tests in `tests/test_issue_doorway.py`: second filing of open fingerprint → `updated`, exactly one create call on the fake gh, occurrence 2 (SC-002); filing after close → `reopened` with regression-marked comment; occurrence comments carry the truncation rule

**Checkpoint**: PR1 scope complete — run `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy`, open PR1 (US1+US2)

## Phase 5: User Story 3 — the problems catalog files through the doorway (P3)

**Goal**: `goal/self_issue.py` creation path delegates to the doorway; exactly one machine-finding issue writer remains.

**Independent Test**: drive the catalog's filing path in the stub environment; assert the resulting issue parses against the schema and the catalog row links it.

- [X] T014 [US3] Add `finding_from_problem(problem, cycle_count)` in `devclaw/goal/self_issue.py` mapping a catalog row to a `MachineFinding` (source `self_issue_catalog`, catalog fingerprint reused, evidence = sample_message + counts, severity from terminal_count, proposed done-when = the recurrence-stops contract)
- [X] T015 [US3] Rewire `run_self_issue_filing` new-issue branch in `devclaw/goal/self_issue.py` to call `issue_doorway.file_finding` with pass-through labels `devclaw:self-filed` + `class:<cat>`; keep `set_problem_issue` linkage and the reopen/close/stale branches' observable behavior byte-compatible (SelfIssueResult, report_line, caps, suppression)
- [X] T016 [US3] Migration tests in `tests/test_issue_doorway_migration.py`: catalog filing produces a schema-conformant body (parses via `parse_machine_issue`) carrying the legacy labels; `issue_number`/`issue_state`/lifecycle behave exactly as before (drive `problem_lifecycle`); existing `tests/test_self_issue.py` stays green with the fake adapter updated only where the body assertion changes
- [X] T017 [US3] Structural guard `tests/test_issue_doorway_single_writer.py` (SC-004): AST walk over `devclaw/` asserting issue-creation call sites (`gh issue create` subprocess args / `create_issue` defs) exist only in `devclaw/issue_doorway.py` and `devclaw/intake.py`; pattern copied from `tests/test_config_single_doorway.py`

**Checkpoint**: exactly one machine-finding issue writer; all three user stories independently proven

## Phase 6: Polish & Cross-Cutting

- [X] T018 Docs honesty: note the doorway in `CLAUDE.md`'s repo map line for root modules if needed, add the module to `docs/architecture.md` only if it makes a doc claim stale; update `docs/INDEX.md` currency tags for any doc touched
- [X] T019 Spec bookkeeping: flip `specs/014-issue-doorway/spec.md` Status from Draft to Implemented; run full gate (`pytest`, `ruff check .`, `mypy`) and open PR2 (US3) with `Closes #666` on the final PR

## Dependencies & Execution Order

- Phase 2 (T001→T002→T003) blocks everything.
- US1: T004 → T005/T006/T007 (parallelizable after T004) → T008 → T009/T010.
- US2: after US1 (extends `file_finding`): T011 → T012 → T013.
- US3: after US1+US2 merged shape exists: T014 → T015 → T016/T017 [P].
- Polish last.

## Implementation Strategy

PR1 = Phases 2–4 (the doorway, complete with dedup — usable by spec 015
immediately). PR2 = Phase 5 + 6 (migration + guard). MVP stop-point after
Phase 4 is legitimate but NOT the plan — the whole spec is the commitment;
both PRs land in this arc, then #666 closes.
