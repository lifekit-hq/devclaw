---
description: "Task list — Speckit Execution Substrate (P1 MVP)"
---

# Tasks: Speckit as the Universal Execution Substrate (retire PLAN.md)

**Input**: Design documents from `/specs/008-speckit-execution-substrate/`
**Prerequisites**: plan.md, spec.md, research.md (D1–D8), data-model.md, contracts/

**Tests**: REQUIRED — the constitution mandates a named regression test per
behavior change. Tests are written first and must fail before implementation.

**Scope**: P1 MVP only (US1 + US2). P2 (US3, US4) and the shrink (#539) are an
outline at the end — **not** expanded into tasks.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: different files, no dependency on an incomplete task → parallelizable.
- **[Story]**: US1 / US2.

## Path Conventions
Brownfield devclaw — edits to existing modules (`devclaw/…`, `runner/…`)
and new tests under `tests/`. No new package.

---

## Phase 1: Setup

- [ ] T001 Confirm the worktree import path resolves to the worktree (`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` must print the worktree path) and capture a green baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` (rules/testing.md).

---

## Phase 2: Foundational (Blocking Prerequisites)

**None.** US1 and US2 are independent (slice-guard/worker/done-gate vs
onboard/delivery). There is no shared blocking prerequisite; both stories may
proceed in parallel after Setup.

---

## Phase 3: User Story 1 — Speckit drives execution; slice-guard reads tasks.md (Priority: P1) 🎯 MVP

**Goal**: The worker advances a feature via speckit (`specs/NNN-*/`), and the
slice-guard's build-ahead detection reads `tasks.md` checkbox flips instead of
`PLAN.md`, staying fail-closed. The done-gate grounds on the spec.

**Independent Test**: Dispatch a feature goal against a speckit repo → a
`specs/NNN-*/` set is produced, one story-slice per PR; the guard reports flips
from `tasks.md` (never reads `PLAN.md`); the done-gate closes on `spec.md`
success criteria.

### Tests for User Story 1 (write first; must FAIL before impl)

- [X] T002 [P] [US1] Load-bearing named regression `tests/test_slice_guard_tasks.py` (SC-003): build a realistic repo (real `git init` + `specs/NNN/tasks.md` with `[ID] [P?] [Story]` items, per `tests/test_review_gate.py` fixture shape); commit a slice flipping >1 item; assert the guard reports the flips **from `tasks.md`** and that **`PLAN.md` is never read** (assert absence). Cases: absent-`tasks.md` → legacy `PLAN.md` fallback (D4); neither present → 0 (fail-open on detection).
- [X] T003 [P] [US1] `tests/test_advance_brief_speckit.py`: the built brief instructs the speckit flow, names `tasks.md`, contains **no** `PLAN.md` directive; assert idle/blocked path adds **zero** cognition (`FakeClaude.calls == 0`, Principle III).
- [X] T004 [P] [US1] `tests/test_done_gate_grounds_on_spec.py`: the done-gate grounds on the executing feature's `specs/NNN/spec.md` success criteria (FR-006); falls back to `done_when` text when no feature dir is recorded.

### Implementation for User Story 1

- [X] T005 [US1] Rewire `devclaw/goal/slice_guard.py`: replace the `PLAN.md` reader (`mega_dump_flips_sync`) with a `tasks_flips_sync(workspace_dir)` that globs `specs/*/tasks.md`, sums `- [ ]`→`- [x]` flips between `HEAD^` and `HEAD` (`git show <ref>:<path>`), falls back to `PLAN.md` when no `tasks.md` exists (D4), and is best-effort/never-raises (fail-open detection). Per `contracts/slice-guard-tasks.md`, D2/D3.
- [X] T006 [US1] Update the call site `devclaw/goal/tick_settle.py` to consume `tasks_flips_sync`; keep the gate **fail-CLOSED under `strict`** / advise-and-ship under `trust` (consequence unchanged — only the source file changed). Per D3. (depends on T005)
- [X] T007 [P] [US1] Rewrite `devclaw/goal/tick.py` `_advance_brief` to instruct the speckit flow (`specify→plan→tasks→implement` for the current feature, smallest not-done story-slice only, no build-ahead) using the repo's `.specify/` scripts as plain markdown — **remove the `PLAN.md` directive**. Model-agnostic (Principle II). Per `contracts/worker-brief.md`, D1.
- [X] T008 [P] [US1] Record the executing feature directory on the goal at dispatch in `devclaw/goal/tick_dispatch.py` (best-effort field on goal status; data-model entity 4) so the done-gate can ground on the right `spec.md`.
- [X] T009 [US1] Rewire the done-gate in `devclaw/goal/evaluator.py` to ground `review_repository` on the executing feature's `specs/NNN/spec.md` success criteria + requirements (FR-006), falling back to `done_when` text when the feature dir is absent. Per D6. (depends on T008)
- [X] T010 [US1] Run US1 tests green: `pytest tests/test_slice_guard_tasks.py tests/test_advance_brief_speckit.py tests/test_done_gate_grounds_on_spec.py`.

**Checkpoint**: US1 functional — speckit drives execution, guard reads `tasks.md`, done-gate on spec.

---

## Phase 4: User Story 2 — Speckit universal: adopt or install, never PLAN.md (Priority: P1)

**Goal**: Every repo devclaw works uses speckit — adopt if `.specify/` present,
install via a reviewable PR if absent. No `PLAN.md` spine.

**Independent Test**: Point devclaw at a `.specify/` repo (adopt, no scaffolding
PR, no `PLAN.md`) and a bare repo (a reviewable install PR, zero silent commits).

### Tests for User Story 2 (write first; must FAIL before impl)

- [X] T011 [P] [US2] `tests/test_onboard_speckit.py` (SC-001/SC-004): `.specify/` present → adopt (assert **no** `PLAN.md` written, **no** PR opened); bare repo → a **reviewable PR** with the `.specify/` scaffold and **zero** direct commits to the default branch; a repo whose install PR is still open → feature dispatch is blocked with an actionable reason.

### Implementation for User Story 2

- [X] T012 [US2] Implement adopt/install detection in the `onboard` tool (`devclaw/server/tools.py`): committed `.specify/` → **adopt** (write no `PLAN.md`); absent → scaffold `.specify/` + open a **reviewable PR** (never a silent commit). Per `contracts/onboard-adopt-install.md`, D5. (new module `devclaw/speckit_setup.py`)
- [X] T013 [US2] Reuse `devclaw/delivery/` to open the install PR (reviewable branch → PR, never a direct default-branch commit). (depends on T012) — `install_speckit_pr` reuses `delivery.deliver_change` on the `devclaw/install-speckit` branch.
- [X] T014 [US2] Block feature dispatch for a repo whose install PR is unmerged (no half-installed state) with an actionable reason (spec Edge Case). (depends on T012) — `_block_if_speckit_pending` in `dispatch_task` (feature kinds only; review is read-only, not blocked).
- [X] T015 [US2] Run US2 test green: `pytest tests/test_onboard_speckit.py`.

**Checkpoint**: US1 + US2 both independently functional — the P1 MVP is code-complete.

---

## Phase 5: Polish & Cross-Cutting

- [X] T016 [P] Docs honesty (same-PR rule): VERIFIED — no stale claim to fix. `CLAUDE.md` has no PLAN.md reference; `docs/architecture.md` carries no PLAN.md-as-spine claim; the arc flow doc `docs/flows/autonomous-issue-pipeline.md:61` already states "slice-guard reads tasks.md (not PLAN.md)". No doc content changed ⇒ no `docs/INDEX.md` currency-tag bump. Architecture.md gets the substrate note when the full arc (through shrink) lands, not mid-transition.
- [X] T017 Full stubbed suite green at ≥ baseline, no idle-token regression: **2188 passed, 5 skipped** (baseline 2182 pre-arc + 6 new tests; independently re-run). Idle `FakeClaude.calls==0` guards green.
- [ ] T018 Tier-B live-shakedown is tracked as **#538** (not a code task) — run per `quickstart.md` + `docs/runbooks/live-shakedown.md` before the shrink slice is started.

---

## Dependencies & Execution Order

- **Setup (T001)** → then US1 and US2 in parallel (independent stories).
- **US1**: T002/T003/T004 (tests, [P]) → T005 → T006; T007/T008 [P]; T009 after T008; T010 gates the phase.
- **US2**: T011 (test) → T012 → T013/T014 → T015.
- **Polish**: T016 [P] anytime after its target changes land; T017 after all impl; T018 (live) after merge.

### Parallel opportunities
- US1 test authoring: T002 ‖ T003 ‖ T004.
- Cross-file impl: T007 ‖ T008 (different files) while T005→T006 proceed.
- Whole stories: US1 ‖ US2 after Setup.

## Implementation Strategy
MVP = US1 + US2. Land US1 first (the load-bearing substitution + fail-closed
guard rewire), then US2. Validate Tier A (T017), open the PR for human review
(the merge backstop), then prove Tier B (#538) before touching the shrink.

---

## Out of scope — outline only (do NOT expand here)

- **US3 (P2)** — label-routed ceremony: feature/enhancement → full cycle;
  bug → `bugfix`; hotfix/`critical-fix` → `hotfix`; chore/docs → direct-advance.
  Adopt community workflows (MartyBonacci/spec-kit-extensions) via speckit's own
  `workflow-registry.json` (FR-009) — **vendor, don't author**. Tracked: #534.
- **US4 (P2)** — migrate existing `PLAN.md`-spine repos to speckit; remove the
  legacy `PLAN.md` fallback (D4). Tracked: #535.
- **Shrink (#539)** — remove the host `investigating→firming→decompose` cognition
  chain (`firming.py`, `decomposer.py`, `checklist.py`, `research.py`,
  `world_research.py`, `planner.py`, ~2,400 lines). **Gated on Tier-B (#538).
  Relocation, not deletion — do not touch until the worker provably owns planning.**
