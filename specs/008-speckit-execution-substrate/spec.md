# Feature Specification: Speckit as the Universal Execution Substrate (retire PLAN.md)

**Feature Branch**: `feat/speckit-execution-substrate`

**Created**: 2026-08-15

**Status**: SHIPPED — in-sandbox speckit execution; Tier-B proven live (#538)

**Input**: User description: "P3 of the arc — devclaw executes every dispatched issue through speckit, universally. No PLAN.md. Speckit is always the discipline: adopt it if present, install it via a reviewable PR if absent, migrate PLAN.md repos to it. Feature issues run the full specify→plan→tasks→implement cycle; bug/chore/docs go direct. The slice-guard reads speckit's tasks.md checkboxes."

## Context & Motivation *(informative)*

Today devclaw hard-codes `PLAN.md` as "the one planning spine": the long-lived advance brief orders the worker to read/maintain `PLAN.md`, and the slice-guard reads `PLAN.md` checkbox flips to detect building ahead. This has three problems: it is **imposed** (devclaw writes a `PLAN.md` into every repo, even one that already uses speckit), it is **blind** to a repo's native discipline (finance-sentry's `specs/001..021` are ignored), and it **does not scale** — a single flat plan bloats and, on a large goal, the decompose call and the condenser-less worker both hit context limits.

P3 replaces the spine: **speckit is the execution substrate everywhere.** This was chosen (2026-08-15) over building a pluggable planning-port with multiple adapters — the operator owns every repo and standardizes on speckit, so the port was premature generalization. Future workflow variation is expressed through **speckit's own `workflow-registry.json`** extension mechanism, not a devclaw abstraction layer. Per-feature `specs/NNN-*/` directories give the bounded scope PLAN.md never had; the `tasks.md` `[ID] [P?] [Story]` checklist is the parseable contract the slice-guard already wants.

This is the largest slice and the last — it touches the worker, the slice-guard, and onboarding at once, and is only worth doing once the loop that feeds it (P1 → P2) is proven.

## Clarifications

### Session 2026-08-15

- Q: How does onboarding decide which planning workflow a repo uses? → A: **Speckit, always.** No detect-and-bind port, no adapter tree. Present → adopt; absent → install. Future custom workflows ride speckit's own `workflow-registry.json`, not a devclaw layer.
- Q: What decides full speckit cycle vs a direct fix? → A: **Label-driven.** feature/enhancement → full specify→plan→tasks→implement; bug/chore/docs → direct-advance, no spec (matches the existing "bug fixes / single bounded PRs need no spec" rule).
- Q: Adopt the OpenSpec-style delta model now? → A: **No — plain per-feature specs first.** speckit's per-feature dirs already bound the monolith; delta/archive is a follow-up that earns its way in once the loop runs.
- Q: When a repo has no planning discipline, does onboarding install speckit? → A: **Yes, via a reviewable PR** (never a silent write). speckit is devclaw's default packed harness.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Speckit drives execution; the slice-guard reads tasks.md (Priority: P1)

A dispatched feature issue is executed through speckit: it becomes a `specs/NNN-feature/` spec → plan → `tasks.md`, is implemented one coherent slice per PR, and the slice-guard's build-ahead detection reads `tasks.md` checkbox flips — not `PLAN.md`. On completion the grounded done-gate closes the issue.

**Why this priority**: This is the substitution — speckit replacing PLAN.md as the thing execution and the guard run on. Everything else in P3 supports it.

**Independent Test**: Dispatch a feature issue against a speckit repo; verify a `specs/NNN-*/` artifact set is produced, the slice-guard reads `tasks.md` checkboxes, and no `PLAN.md` is written or read.

**Acceptance Scenarios**:

1. **Given** a speckit repo and a dispatched feature issue, **When** it executes, **Then** a per-feature spec/plan/tasks set is produced and implemented one slice per PR.
2. **Given** an in-flight feature, **When** the slice-guard checks for build-ahead, **Then** it reads `tasks.md` checkbox flips (not `PLAN.md`).
3. **Given** execution completes, **When** the done-gate runs, **Then** it evaluates against the spec and closes the issue on `achieved`.

---

### User Story 2 - Speckit is universal: adopt or install, never PLAN.md (Priority: P1)

Every repo devclaw works uses speckit. If `.specify/` is present, devclaw adopts it. If absent, onboarding installs speckit into the repo as a reviewable PR. No repo uses `PLAN.md` as the planning spine anymore.

**Why this priority**: Universality is the decision — without it you have two disciplines competing again.

**Independent Test**: Point devclaw at a speckit repo (verify adopt, no scaffolding PR) and a bare repo (verify a reviewable speckit-install PR, no silent commit); confirm neither ends up with a `PLAN.md` spine.

**Acceptance Scenarios**:

1. **Given** a repo with `.specify/`, **When** devclaw onboards/works it, **Then** it adopts the existing speckit setup and writes no `PLAN.md`.
2. **Given** a bare repo, **When** onboarding runs, **Then** speckit is scaffolded via a reviewable PR (not a silent write).
3. **Given** any repo, **When** devclaw plans work, **Then** no `PLAN.md` is created or maintained as the spine.

---

### User Story 3 - Label-routed ceremony (Priority: P2)

A dispatched issue labeled feature/enhancement runs the full speckit cycle; a bug/chore/docs issue goes direct-advance with no spec. Trivial work does not pay spec-cycle ceremony.

**Why this priority**: Prevents the "reinvented waterfall" trap — full ceremony on a typo is a cost, not a virtue.

**Independent Test**: Dispatch a feature issue and a bug issue; verify the feature produces a `specs/NNN-*/` set and the bug produces a direct fix with no spec dir.

**Acceptance Scenarios**:

1. **Given** a feature/enhancement-labeled issue, **When** executed, **Then** the full specify→plan→tasks→implement cycle runs.
2. **Given** a bug/chore/docs-labeled issue, **When** executed, **Then** it is fixed direct-advance with no spec directory created.

---

### User Story 4 - PLAN.md is retired and migrated (Priority: P2)

Repos currently driven by devclaw's `PLAN.md` are migrated to speckit, and the slice-guard stops reading `PLAN.md` entirely.

**Why this priority**: Closes out the old spine so there is exactly one discipline, not a lingering dual-mode.

**Independent Test**: Take a repo with an existing devclaw `PLAN.md`; verify migration to speckit and that the guard reads `tasks.md`, not `PLAN.md`, afterward.

**Acceptance Scenarios**:

1. **Given** a repo with a devclaw `PLAN.md` spine, **When** migrated, **Then** its plan is expressed as speckit `specs/` and the guard reads `tasks.md`.
2. **Given** migration is complete, **When** devclaw works the repo, **Then** `PLAN.md` is no longer read for any decision.

---

### Edge Cases

- **Feature vs bug label ambiguous/absent**: default to the more careful path (treat as feature → full cycle) or bounce to needs-human for labeling; never silently guess a direct fix on an unlabeled ask.
- **Bare-repo install PR not yet merged**: devclaw does not execute feature work in that repo until the speckit scaffolding is in place (no half-installed state).
- **Autonomous execution + speckit's interactive clarify step**: because async-clarify is deferred (P2) and readiness is gated at intake (P1), autonomous execution runs the mechanical speckit steps **without** an interactive human clarify checkpoint; an under-specified spec fails firming and bounces to needs-human rather than blocking on a question.
- **A feature too large for one spec**: speckit's per-feature slicing + the P1/P2/P3 story grouping bound it; the slice-guard enforces one-slice-per-PR. It never becomes one flat plan.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: devclaw MUST use speckit as the planning/execution discipline for every repo it works; `PLAN.md` MUST NOT be used as the planning spine.
- **FR-002**: If a repo has speckit (`.specify/`), devclaw MUST adopt it — its `specs/`, `tasks.md`, and constitution — and MUST NOT write a `PLAN.md`.
- **FR-003**: If a repo lacks speckit, onboarding MUST install it via a reviewable PR; it MUST NOT silently commit the scaffolding.
- **FR-004**: A dispatched issue labeled feature/enhancement MUST run the full speckit cycle (specify→plan→tasks→implement); a bug/chore/docs issue MUST go direct-advance with no spec.
- **FR-005**: The slice-guard MUST read the bounded checklist from speckit's `tasks.md` (`[ID] [P?] [Story]` checkbox format) and MUST NOT read `PLAN.md`.
- **FR-006**: On completion, the executed issue MUST be judged by the grounded done-gate; a non-achievable spec MUST bounce to needs-human.
- **FR-007**: Existing `PLAN.md`-spine repos MUST be migrated to speckit; after migration `PLAN.md` MUST NOT be read for any decision.
- **FR-008**: Autonomous execution MUST run speckit's mechanical steps without an interactive human clarify checkpoint (async-clarify deferred, readiness gated at intake); an under-specified spec fails firming → needs-human.
- **FR-009**: Future custom or alternative workflows MUST be expressed via speckit's own `workflow-registry.json` extension mechanism; devclaw MUST NOT build a separate planning-source abstraction/port.
- **FR-010**: The worker layer MUST remain model-agnostic — speckit is consumed as plain markdown/commands, not vendor tool-wiring.

### Key Entities *(include if feature involves data)*

- **Speckit setup**: `.specify/` (constitution, templates, workflow-registry) + `specs/NNN-*/` (spec.md, plan.md, tasks.md).
- **tasks.md checklist**: `[ID] [P?] [Story]` checkbox items — the slice-guard's build-ahead contract and the per-story shippable slices.
- **Issue label**: routes full-cycle vs direct-advance (feature/enhancement vs bug/chore/docs).
- **Install PR**: the reviewable scaffolding change for a bare repo.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 repos use `PLAN.md` as the planning spine after migration.
- **SC-002**: 100% of feature-labeled dispatched issues produce a speckit spec/plan/tasks artifact set.
- **SC-003**: The slice-guard's build-ahead detection operates off `tasks.md` checkbox flips (named regression test), with `PLAN.md` no longer read.
- **SC-004**: A bare repo receives speckit via a reviewable PR — 0 silent scaffolding commits.
- **SC-005**: 0 spec directories are created for bug/chore/docs-labeled issues (trivial work skips the cycle).

## Assumptions

- **No planning-port/adapter abstraction is built** (2026-08-15 decision). Speckit is universal; heterogeneity is handled inside speckit's `workflow-registry.json`, not a devclaw layer.
- The **OpenSpec-style delta model is a follow-up**, not part of P3 — per-feature `specs/` already bounds the monolith for now.
- Autonomous execution **skips interactive clarify** (FR-008), derived from the P2 async-clarify deferral; the intake-readiness gate (P1) + firming bounce cover ambiguity.
- Label taxonomy reuses the existing P0–P2 + area labels plus type labels (feature/bug/chore/docs).
- Migration of existing `PLAN.md` repos is a one-time, reviewable change per repo.

## Out of Scope *(named for later slices / follow-ups)*

- **OpenSpec-style delta/archive model** — the strongest anti-drift mechanism; a follow-up once the loop is proven.
- **Async-clarify** — deferred (its own slice).
- **Custom non-speckit workflows** — future, and via speckit's own registry, not a devclaw port.
- **Mechanical governors** (patch-ID dedup, per-goal compute ceiling, conflict detection) — folded in as small follow-ups, not P3 scope.
- **Task-level issue mirror (observability)** — optionally publishing `tasks.md` tasks as read-only GitHub issues that close on checkbox-flip, for task-granular visibility. A follow-up, NOT core; `tasks.md` stays the single execution source of truth. See the `taskstoissues` note in Rejected Alternatives.

## Rejected Alternatives *(direction memory — do not re-litigate without new evidence)*

- **Detect-and-bind planning PORT with multiple adapters (speckit / github-issues / legacy-PLAN.md).** Considered and **rejected 2026-08-15** (reversing the earlier brainstorm recommendation): the operator owns all repos and standardizes on speckit, so a pluggable port is premature generalization. Future heterogeneity is handled via speckit's own `workflow-registry.json`, not a devclaw abstraction. Revisit only if devclaw must work a repo whose owner mandates a non-speckit discipline.
- **Keep `PLAN.md` (or a per-goal scoped `PLAN.md`).** Rejected: it is imposed, blind to repo-native discipline, and doesn't scale — the whole motivation for the slice.
- **Always run the full spec cycle (even trivial fixes).** Rejected: the reinvented-waterfall trap; label-routing keeps trivial work cheap.
- **Adopt the delta model in P3.** Deferred, not rejected — it earns its way in as a follow-up once the loop runs.
- **Use speckit `taskstoissues` as an issue-creator / execution unit.** Considered and **rejected 2026-08-15**. It runs the wrong direction (tasks→issues; our spine is issue→tasks), and its issues would be created outside `file_intake` — **bypassing the P1 readiness gate**, the exact control we are building. It is also a one-shot generator (no close-sync, no epic linkage) and strips `[P]`/`[US#]` markers. The claim/execute/done-gate unit stays the **feature-issue** (task-issues would fragment feature-coherence and ~10× the governance surface). Its create+dedup *pattern* is retained only for the optional read-only task-mirror follow-up in Out of Scope.

## Constitution Alignment *(no amendment required)*

- **Model-agnostic worker layer** — speckit is consumed as plain markdown/commands (FR-010), consistent with the invariant.
- **Fail-closed slice-guard** — still fails closed, now reading `tasks.md` instead of `PLAN.md` (FR-005).
- **Done is a proposal, gated on grounded evaluation** — the done-gate is unchanged (FR-006).
- **Single source of truth / generated views** — speckit artifacts are the plan; human views mirror, never drive.
