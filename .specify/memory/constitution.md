# devclaw Constitution

Seeded 2026-08-13 from the load-bearing invariants in `CLAUDE.md` and
`docs/architecture.md`. Those documents remain canonical — on any conflict,
`CLAUDE.md` wins and this file must be corrected in the same PR. This
constitution exists so the speckit pipeline checks devclaw's *actual* rules
instead of a generic product-feature template.

## Core Principles

### I. OAuth only — never metered billing
Cognition is always `claude` over Pro/Max OAuth. `ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` are actively stripped at every spawn site. No spec may
introduce a path that lets a stray key silently switch autonomous runs onto
metered billing.

### II. Model-agnostic worker layer
Skills are plain markdown; hooks are bash `.sh` files; cross-tool capability
goes through MCP, not vendor tool-wiring. Swapping `claude-code` for another
agent must only change the `ACPAgent` call.

### III. Zero-token idle
An idle goal and an in-flight-still-running goal cost ~0 `claude` calls.
Cheap SQLite/timestamp checks run before any LLM call. A spec that adds
tick-path cognition firing on idle is unconstitutional on its face.

### IV. Single writer to state
Only the TaskQueue mutates task rows; goal state is owned by `GoalStore`
behind CAS'd transitions (`TransitionConflict` over silent clobber).
Markdown status files are generated views, never read back for decisions.

### V. Verification fails closed; "done" is a proposal
A quality-gate crash is not an approval. Completion is gated on grounded
evaluation (`review_repository` against the firmed `done_when`), never on
counting PRs or backlog items. Fail-closed-on-crash governs every gate that is
*consulted*: a gate that runs and cannot produce a verdict never ships on its
own silence (#186). The strictness dial sets which gates are *consulted*, not
just their consequence: under `trust` (the default) the per-increment
adversarial diff review is **not part of the task gate chain at all** — the
human reviews every PR and the goal-level done-gate re-catches its findings —
while under `strict` it is consulted and fail-closed exactly as before. A gate
that is by policy not consulted produces no silence to ship on, so removing it
from the `trust` chain does not repeal #186. The verify, test-integrity, and
goal-level done gates stay always-hard in BOTH modes; the browser-E2E gate
stays dial-able (advise-under-trust / block-under-strict) per ADR 0007.

### VI. Loud failure over silent degradation
Broken delivery fails; lost/corrupt state blocks legibly with an owner ping;
usage limits pause-and-resume with WIP preserved. Any bounded coverage
(top-N, sampling, truncation) says so out loud.

### VII. Fix the class, not the instance
A concrete failure is an instance of a class; change the rule, not the case
that hurt today. Domain specifics (code, PRs, Playwright) stay at the edges
so layers 1–4 remain domain-agnostic.

## Development Workflow

- ALL behavior-changing work starts with the speckit pipeline —
  `/speckit-specify` → `/speckit-clarify` (run WITH Denys, one question at a
  time) → `/speckit-plan` → `/speckit-tasks` → implement
  (see `.claude/rules/speckit-workflow.md`). No implementation before the
  clarify step is done. The spec records rejected alternatives — it is the
  direction memory; the retired proposal→ADR pipeline
  (`docs/proposals/` + `docs/decisions/`) is frozen history.
- Every behavior-change PR ships a named regression test; zero-token guard
  tests (`FakeClaude.calls == 0`) are load-bearing — if one fails, the change
  is wrong, never the test.
- Branch per change; squash merges; docs made stale by a diff are fixed in
  the same PR.
- Slice novel work into independently-shippable P1/P2/P3 increments; firm and
  size only P1, in devclaw's own units (N PRs, an end-of-week cap).

## Governance

This constitution is the invariant statement the speckit pipeline checks
specs against — the enforcement surface for dev work on this repo (ruled by
Denys 2026-08-13, retiring the proposal→ADR pipeline and the invariant-guard
agent). `CLAUDE.md` remains the repo contract: on any conflict `CLAUDE.md`
wins and this file is corrected in the same PR. A spec that requires an
invariant change must say so explicitly and amend this constitution in the
same arc — never silently.

**Version**: 2.1.0 | **Ratified**: 2026-08-13 | **Last Amended**: 2026-08-14
(2.1.0 — Principle V: the strictness dial sets which gates are *consulted*; under
`trust` the per-increment adversarial diff review is dropped from the task gate
chain. Spec `001-review-gate-repositioning`.)
