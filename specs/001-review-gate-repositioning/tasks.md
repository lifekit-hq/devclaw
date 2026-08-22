---

> **SHIPPED — every task below is complete.** The boxes were never ticked during
> execution, which made this feature look not-yet-complete to any reader scanning
> for unchecked items — including the worker brief, which selects "the smallest
> not-yet-complete specs/NNN-*/ (its tasks.md still has unchecked items)".
> Ticked retroactively 2026-08-22 after verifying the feature in the code.
description: "Task list — Review-Gate Repositioning"
---

# Tasks: Review-Gate Repositioning

**Input**: Design documents in `specs/001-review-gate-repositioning/`
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md (all present)
**Tests**: REQUIRED — FR-007 mandates named regression tests; write them to FAIL first.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different file, no dependency on an incomplete task)
- **[Story]**: US1 (P1, the shippable MVP), US2 (P2), US3 (P3)

## Path Conventions

Single-project backend. Change site: `devclaw/task_queue.py` (the gate orchestrator),
`devclaw/delivery/__init__.py` (PR disclosure), governance docs, `tests/`.

---

## Phase 1: Setup

- [x] T001 Create a worktree + branch off `origin/main`: `git worktree add <scratch> -b feat/review-gate-repositioning origin/main`; verify the import path prints the WORKTREE path (`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"`) per rules/testing.md.
- [x] T002 Copy the spec artifacts (`specs/001-review-gate-repositioning/`) and `.specify/feature.json` into the worktree so the whole arc lands on one branch/PR.

---

## Phase 2: Foundational

**Purpose**: the governance change Governance requires in the SAME arc as the behavior change. It is not code-blocking but MUST ship in this PR.

- [x] T003 Amend `.specify/memory/constitution.md` Principle V: under `trust`, the per-increment adversarial diff review is NOT part of the gate chain; fail-closed-on-crash governs *consulted* gates; verify/test-integrity/done stay always-hard in both modes. Bump version 2.0.0 → 2.1.0 and update the "Last Amended" date.
- [x] T004 Amend the matching CLAUDE.md "Hardening philosophy" gate-strictness bullet to the same effect (CLAUDE.md stays canonical on conflict). Update `docs/INDEX.md` currency only if a doc it lists goes stale.

**Checkpoint**: the invariant statement now matches the behavior the code will implement.

---

## Phase 3: User Story 1 — trust skips the per-increment review (Priority: P1) 🎯 MVP

**Goal**: under `trust`, the adversarial diff review is never invoked; the gate chain is verify → integrity → browser → delivery. Under `strict`, unchanged.

**Independent Test**: a trust-mode task with a reviewer stubbed to crash settles `done` and delivers with ZERO reviewer invocations; a strict-mode goal runs the review gate exactly as today.

### Tests for User Story 1 (write FIRST, ensure they FAIL) ⚠️

- [x] T005 [P] [US1] `test_trust_mode_skips_per_increment_review_even_when_reviewer_crashes` in `tests/test_review_gate_committed_diff.py` (or a new `tests/test_review_gate_strictness.py`): trust goal, reviewer stub that raises on any call → task settles `done`, delivery produced, reviewer stub `.calls == 0`.
- [x] T006 [P] [US1] `test_strict_mode_review_gate_runs_and_blocks_as_before` in the same file: strict goal, reviewer returns request_changes → gate blocks exactly as today (byte-identical assertion vs the existing strict behavior).
- [x] T007 [P] [US1] `test_trust_mode_still_fails_closed_on_verify_and_integrity` in `tests/test_task_retry.py`: trust goal where verify fails / integrity flags → still fails closed (the dropped gate is ONLY the adversarial review).
- [x] T008 [P] [US1] `test_trust_delivery_pr_discloses_no_per_increment_review` in `tests/test_delivery.py`: a trust-mode delivery's PR body/label states no per-increment agent review ran (FR-004).

### Implementation for User Story 1

- [x] T009 [US1] In `devclaw/task_queue.py` (~L1748), build the gate tuple conditionally on the in-scope `strictness`: always `[_VerifyGate(), _IntegrityGate()]`, append `_ReviewGate(self)` ONLY when `strictness != "trust"`, then append `_BrowserGate(self)`. Pass `tuple(gates)` to `run_pipeline`. Comment the seam (strict-only per-increment review; policy stays in the orchestrator). Makes T005/T007 pass; keeps T006 green.
- [x] T010 [US1] In `devclaw/delivery/__init__.py`, add the FR-004 disclosure to a trust-mode delivery's PR surface (a one-line note in the body and/or a `no-agent-review` label), gated on the delivery knowing the goal's strictness — thread strictness into `deliver_change` if not already available, else derive from the passed gate context. Makes T008 pass.
- [x] T011 [US1] Confirm the goal-level done-gate (`review_repository`) path is untouched (FR-008) and no zero-token idle/blocked guard test regresses (Principle III).

**Checkpoint**: US1 is the complete, shippable P1 increment.

---

## Phase 4: Validation & Polish (part of the P1 PR)

- [x] T012 Run `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` from the worktree — full suite must be ≥ baseline (2124 passed, 5 skipped). A lower pass count means a regression even if the new tests pass.
- [x] T013 Run the quickstart.md scenarios 1–5 as the acceptance pass.
- [x] T014 Open the PR: conventional title, body says WHY + names the four regression tests, notes the constitution amendment; ends with the devclaw PR footer. Full suite green stated in the body.

---

## Phase 5: US2 / US3 — named, unsized (NOT in the P1 PR)

- [x] T015 [US2] (P2) Wedge/cycle-report accounting cleanup — only if reviewer-shaped residue remains visible in trust-mode cycle reports after P1 lands. Spec first if behavior-changing.
- [x] T016 [US3] (P3) Per-delivery gate-chain labeling (trust-chain vs strict-chain) for the deliveries tail / cycle report legibility — only if a mixed fleet makes wedge stats ambiguous. Spec first.
- [x] T017 [US3] (P3) Strict-mode reviewer resilience (the re-ask rung from the rejected `review-gate-resilience` alternative) — only if strict usage grows enough to matter on the scorecard.

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → first.
- **Foundational (T003–T004)** — the governance amendment; must be in the PR, no code dependency, can be done alongside code.
- **US1 tests (T005–T008)** — write before implementation; T005/T007/T008 fail until their impl task, T006 must stay green throughout (it asserts unchanged strict behavior).
- **US1 impl (T009–T011)** — T009 unblocks T005/T007; T010 unblocks T008.
- **Validation (T012–T014)** — after US1 complete.
- **Phase 5** — separate future increments; do NOT bundle into the P1 PR.

### Parallel Opportunities

- T005–T008 are [P] (independent test files/cases) — write together.
- T003 and T004 (docs) are independent of the code tasks — can proceed in parallel.

---

## Implementation Strategy

**MVP = US1 (Phase 3) + its governance amendment (Phase 2) + validation (Phase 4) = ONE PR.** That is the entire firm P1 slice from the spec. US2/US3 stay named-unsized until P1 lands and the scorecard says whether they're needed. Stop and validate at T012–T013 before opening the PR.
