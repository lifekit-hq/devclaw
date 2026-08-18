---
description: "Task list — Speckit Execution Substrate (P1 MVP)"
---

# Tasks: Speckit as the Universal Execution Substrate (retire PLAN.md)

**Input**: Design documents from `/specs/008-speckit-execution-substrate/`
**Prerequisites**: plan.md, spec.md, research.md (D1–D8), data-model.md, contracts/

**Tests**: REQUIRED — the constitution mandates a named regression test per
behavior change. Tests are written first and must fail before implementation.

**Scope**: P1 MVP (US1 + US2) — SHIPPED (PR #540, Tier-B proven #538).
**US3 expanded 2026-08-18** (Phase 6, T019+) per the tier-ladder plan update
(research D9–D13 + resolved questions). US4 and the shrink (#539) remain an
outline at the end — **not** expanded into tasks.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: different files, no dependency on an incomplete task → parallelizable.
- **[Story]**: US1 / US2.

## Path Conventions
Brownfield devclaw — edits to existing modules (`devclaw/…`, `openhands-runner/…`)
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

## Phase 6: User Story 3 — Label-routed ceremony tiers (Priority: P2)

**Goal**: Route dispatched work to the ceremony tier its label/kind earns —
full / bugfix / hotfix / direct — with the middle tiers vendored from
MartyBonacci/spec-kit-extensions (frozen, pinned) and routing decided
mechanically host-side. Ambiguity routes only UP. Per `contracts/tier-routing.md`,
data-model §6–7, research D9–D13.

**Independent Test**: quickstart.md US3 — feature issue → full `specs/NNN-*/`;
bug issue → `specs/bugfix-NNN-*/` with regression test before fix; docs issue →
direct fix, zero artifact dirs; delivery stays on the goal branch in all three.

**Ships as ~2 PRs**: Slice A (T019–T023, vendor+register) and Slice B
(T024–T029, route+stamp) — independently mergeable in that order.

### Slice A — vendor + register (PR 1)

- [ ] T019 [P] [US3] Vendor-integrity named regression `tests/test_speckit_vendor_pack.py` (write first; must FAIL): asserts `.specify/extensions/workflows/bugfix/` and `.specify/extensions/workflows/hotfix/` exist in devclaw's scaffold source; both `create-*.sh` scripts honor the `SPECKIT_NO_BRANCH` guard (assert the guard string is present — the one allowed upstream delta); a vendor README records the pinned upstream SHA + MIT license; `scaffold_specify()` copies `extensions/` into a bare-repo install.
- [ ] T020 [US3] Vendor the pack into devclaw's own `.specify/extensions/workflows/{bugfix,hotfix}/`: copy the two workflow templates + `commands/speckit.bugfix.md` + `commands/speckit.hotfix.md` + `scripts/create-bugfix.sh` + `scripts/create-hotfix.sh` from MartyBonacci/spec-kit-extensions at ONE pinned commit SHA (record it); patch both scripts so branch creation/checkout is skipped when `SPECKIT_NO_BRANCH=1` (D12 — delivery owns branches, #486); write `.specify/extensions/workflows/README.md` (upstream SHA, license, the single delta, the #2319 delete-then-reinit upgrade note). `modify`/`refactor`/`deprecate` are NOT vendored.
- [ ] T021 [US3] Register in the packed harness: add `"extensions"` to `_SCAFFOLD_DIRS` in `devclaw/speckit_setup.py` so adopt/install repos receive the tier workflows; add registry entries via speckit's own mechanism (FR-009) if `.specify/workflows/` requires them for discovery — never a devclaw abstraction. (depends on T020)
- [ ] T022 [P] [US3] Worker skill content: add `openhands-runner/skills/_writes-code/06-speckit-tiers.md` — plain markdown (Principle II) telling the worker how to execute a stamped bugfix/hotfix/direct tier (bugfix: bug report artifact, regression test BEFORE the fix, minimal plan/tasks; hotfix: expedited + post-mortem section; direct: smallest fix, create NO `specs/` artifacts), pointing at the `.specify/extensions/` paths, with `SPECKIT_NO_BRANCH=1` set in the run environment. NOTE in the PR body: **sandbox image rebuild required on next deploy** (skills are baked in).
- [ ] T023 [US3] Slice-A gate: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_speckit_vendor_pack.py` green, then full suite ≥ baseline. (depends on T019–T022)

### Slice B — route + stamp (PR 2)

- [ ] T024 [P] [US3] Named regression `tests/test_tier_routing.py` (write first; must FAIL): table-driven over every row of `contracts/tier-routing.md` Behavior; the monotone property (adding any unknown label never yields a lighter tier than without it); conflicting labels (`feature`+`bug`) → full; `review_repository` → no tier.
- [ ] T025 [P] [US3] Named regression `tests/test_advance_brief_tiers.py` (write first; must FAIL): the brief stamps the tier-specific block per tier; the `direct` brief forbids artifact creation — assert presence AND absence per the cognition-prompts rule (prove the marker absent from the raw template first); full-tier brief is byte-compatible with today's speckit flow text; idle/blocked paths still add zero cognition (`FakeClaude.calls == 0`).
- [ ] T026 [US3] Implement `devclaw/goal/tier_routing.py`: pure `route_tier(kind: str | None, labels: list[str] | None) -> Tier` dict lookup (no I/O, no LLM) + the per-tier brief clause each `Tier` carries. Per `contracts/tier-routing.md` — highest-ceremony match wins; no signal/unknown/conflict → full.
- [ ] T027 [US3] Wire the companion path: in `devclaw/server/tools.py::dispatch_task`, `kind="fix_bug"` routes through `route_tier` and stamps the bugfix-tier block into the task brief; `implement_feature` stamps full. The `kind` enum is UNCHANGED (resolved: no `hotfix` kind — hotfix is label-only). (depends on T026)
- [ ] T028 [US3] Wire the goal path: where the advance target is a labeled issue, fetch labels mechanically at dispatch (best-effort `gh` subprocess at the existing dispatch-time call site in `devclaw/goal/tick_dispatch.py` — never idle-path; fetch failure ⇒ no labels ⇒ full tier) and pass the resolved `Tier` into `_advance_brief` (`devclaw/goal/tick.py`) to emit the tier block. The worker never re-decides the tier (#358 class stays host-enforced). (depends on T026)
- [ ] T029 [US3] Slice-B gate: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_tier_routing.py tests/test_advance_brief_tiers.py` green, then full suite ≥ baseline with all `FakeClaude.calls == 0` idle guards green. (depends on T024–T028)

### US3 Polish

- [ ] T030 [P] [US3] Docs honesty (same-PR rule): sweep `docs/flows/autonomous-issue-pipeline.md` + any doc stating the binary feature-vs-direct routing; update to the tier ladder; bump touched docs' currency tags in `docs/INDEX.md`.
- [ ] T031 [US3] File the survey follow-up issues on lifekit-hq/devclaw (from research.md resolved-Q3): Quratulain `bugfix` as optional `after_implement` spec-consistency hook for feature-tier work; evaluate `fix-findings` + `ci-guard`; the 0.16.4 hop (RunState TOCTOU); the #2319 vendored-upgrade procedure. Close #534 via the Slice-B PR.
- [ ] T032 [US3] Tier-B live validation per quickstart.md US3 (three labeled issues against a speckit repo) — tracked as a live-shakedown run, not a code task; AFTER both PRs merge + sandbox image rebuild.

---

## Dependencies & Execution Order

- **Setup (T001)** → then US1 and US2 in parallel (independent stories).
- **US1**: T002/T003/T004 (tests, [P]) → T005 → T006; T007/T008 [P]; T009 after T008; T010 gates the phase.
- **US2**: T011 (test) → T012 → T013/T014 → T015.
- **Polish**: T016 [P] anytime after its target changes land; T017 after all impl; T018 (live) after merge.
- **US3** (after P1 shipped — already true): Slice A T019 (test) → T020 → T021; T022 [P] alongside T020/T021; T023 gates PR 1. Slice B T024/T025 (tests, [P]) → T026 → T027 ‖ T028 → T029 gates PR 2. Slice B merges after Slice A. T030 rides whichever PR touches the docs' claims; T031 after PR 2; T032 after both PRs + image rebuild.

### Parallel opportunities
- US1 test authoring: T002 ‖ T003 ‖ T004.
- Cross-file impl: T007 ‖ T008 (different files) while T005→T006 proceed.
- Whole stories: US1 ‖ US2 after Setup.
- US3: T019 ‖ T022 (different trees); T024 ‖ T025; T027 ‖ T028 (different files, both after T026).

## Implementation Strategy
MVP = US1 + US2. Land US1 first (the load-bearing substitution + fail-closed
guard rewire), then US2. Validate Tier A (T017), open the PR for human review
(the merge backstop), then prove Tier B (#538) before touching the shrink.

---

## Out of scope — outline only (do NOT expand here)

- ~~US3~~ — **expanded into Phase 6 (T019–T032) on 2026-08-18**; tracked: #534.
- **US4 (P2)** — migrate existing `PLAN.md`-spine repos to speckit; remove the
  legacy `PLAN.md` fallback (D4). Tracked: #535.
- **Shrink (#539)** — remove the host `investigating→firming→decompose` cognition
  chain (`firming.py`, `decomposer.py`, `checklist.py`, `research.py`,
  `world_research.py`, `planner.py`, ~2,400 lines). **Gated on Tier-B (#538).
  Relocation, not deletion — do not touch until the worker provably owns planning.**
