# Research — Review-Gate Repositioning

Phase 0. The spec was already clarified with Denys (2026-08-14), so the only open
questions were *how* to implement the trust-mode skip cleanly against the existing
gate architecture. All resolved below; no `[NEEDS CLARIFICATION]` remain.

## Decision 1 — Where the trust-mode skip lives: orchestrator gate-list filter

**Decision**: Omit `_ReviewGate(self)` from the gate tuple built at
`devclaw/task_queue.py:~1748` when `strictness == "trust"`. The gate classes and
`gate_pipeline`/`gate_policy` are untouched.

**Rationale**: `gate_pipeline.py` states the design rule explicitly — *"Policy never
moves into a gate"* — and `GateInput` deliberately does **not** carry the strictness
dial; strictness is applied by the orchestrator via `gate_consequence(gate_id,
strictness)`. The orchestrator already has `strictness` in scope
(`row.strictness`, ~L1562). Filtering the gate list there keeps the dial where it
belongs and is the smallest possible change (one conditional).

**Alternatives considered**:
- *`applies()`-based self-skip on the gate*: add `strictness` to `GateInput` and
  have `_ReviewGate.applies()` return `False` under trust. Rejected: it threads
  policy into the gate and grows the `GateInput` contract — the precise coupling
  the pipeline design forbids. (The existing `scaffold` self-skip is about the
  *input's nature*, not the *policy dial* — not a precedent for strictness.)
- *Keep review running under trust, downgrade its crash to advisory*: rejected in
  the spec — repeals fail-closed-on-crash for a consulted gate (Principle V
  violation with no clean reading).

## Decision 2 — Interaction with the existing `review_gate` enable toggle

**Decision**: Leave `_review_gate_enabled(workspace_dir)` /
`REVIEW_GATE_ENABLED` / the per-project `review_gate` override as-is. The new
trust-skip is an **additional, orthogonal** reason the review gate does not run;
it composes with the existing enable flag (either one being off ⇒ no review).

**Rationale**: The per-project toggle is an operator override for specific repos;
the strictness dial is the goal-level default. They answer different questions and
should stack, not replace each other. No migration.

## Decision 3 — The done-gate stays

**Decision**: The goal-level `review_repository` done-check keeps its `claude`
call and always-hard semantics in both modes; it is not in this task-gate tuple.

**Rationale**: Fresh-context agent review's value scales with the size of the
claim and whether a human also checks it. "This goal is done" is a large claim
checked once per cycle with no human awake — the highest-value place to keep an
LLM reviewer. Review-shaped cognition wedges therefore drop to once-per-cycle with
bounded input, not to zero (SC-001 is scoped to the *per-increment* review only).

## Decision 4 — Constitution/CLAUDE.md amendment shape

**Decision**: Amend Principle V to state that under `trust` the per-increment
adversarial diff review is not part of the gate chain, and that
fail-closed-on-crash governs *consulted* gates; verify/test-integrity/done stay
always-hard in both modes. Mirror the one-line change in CLAUDE.md's "Hardening
philosophy" gate-strictness bullet. Bump the constitution version (2.0.0 → 2.1.0,
minor: a materially expanded principle, not a breaking re-ratification).

**Rationale**: Governance requires a spec that changes an invariant to amend the
constitution in the same arc; CLAUDE.md remains canonical on conflict, so both
move together.

## Out of scope (named, deferred)

- **P2** — wedge/cycle-report accounting cleanup if any reviewer-shaped residue
  remains visible in trust-mode cycle reports after P1.
- **P3** — strict-mode reviewer resilience (the re-ask rung from the rejected
  `review-gate-resilience` alternative) — only if strict usage grows enough to
  matter on the scorecard.
