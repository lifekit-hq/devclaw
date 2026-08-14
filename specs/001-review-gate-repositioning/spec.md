# Feature Specification: Review-Gate Repositioning

**Feature Branch**: `001-review-gate-repositioning`

**Created**: 2026-08-14

**Status**: Draft

**Input**: User description: "review-gate-repositioning — per-increment adversarial diff review becomes strict-mode-only; trust mode gates on tests + E2E + test-integrity, with the done-gate and human PR review as the semantic backstop."

## Problem Statement

The per-increment adversarial diff review is the #1 mechanism-wedge source on the
live instance: `cognition|review exit=-9` ×117 lifetime terminal occurrences,
review timeouts ×7/×12 (issues #452/#422), and a non-JSON prose crash on a
PLAN.md-only diff on the night of 2026-08-13 (task `b8f23f4f`). Of the last 7
active nightly cycles, 6 were wedged, with review-class wedges dominant.

Under the default `trust` strictness the gate's **findings** are advisory
(advise-and-ship, ADR 0007) while its **infrastructure crashes** fail the task
closed — an inversion where the gate's crashes are harder than its verdicts.
Under companion mode (the primary use-mode: PR within the hour, the operator
reviews every PR before merge) the per-increment LLM reviewer is redundant with
the human review, and its unique catches (empty increment, spec-fidelity drift)
are re-caught by the done-gate one cycle later — demonstrated live: the
done-gate correctly bounced premature "done" proposals on `ledger-2026-08-12`
(×2) and `ledger-2026-08-10` (×3).

The underlying judgment: fresh-context agent review's value is proportional to
the size of the claim being checked and who else checks it. "This goal is done"
is a large claim checked once per cycle with no human awake — keep the LLM
reviewer there. "This increment is good" is a small claim already checked by
executable tests before it and a human after it — drop the LLM reviewer there
under trust.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trust-mode nights stop dying on reviewer infrastructure (Priority: P1)

As the operator running overnight goals under the default `trust` strictness,
a task whose code passes the executable gates (tests, browser E2E when
frontend changed, test-integrity) is delivered as a PR without consulting the
per-increment adversarial diff reviewer — so a reviewer-infrastructure failure
(process kill, timeout, unparseable response) can no longer fail a task whose
work was otherwise verified. The morning cycle report reflects mechanism
health, not the fragility of an advisory component.

**Why this priority**: This is the entire point of the feature — it removes the
single largest wedge class from the default operating mode. Everything else in
this spec is a guardrail around this change.

**Independent Test**: Run a trust-mode task to settlement with the reviewer
component stubbed to crash unconditionally; the task must settle `done` with a
delivered PR, and the reviewer stub must show zero invocations.

**Acceptance Scenarios**:

1. **Given** a trust-mode goal with a task whose verify, browser (if
   triggered), and test-integrity gates pass, **When** the task settles,
   **Then** it is delivered as a PR and the per-increment diff reviewer was
   never invoked.
2. **Given** the same task with the reviewer component configured to crash on
   any call, **When** the task settles, **Then** the outcome is identical to
   scenario 1 (the crash cannot occur because the call does not happen).
3. **Given** a trust-mode delivery, **When** the operator opens the PR,
   **Then** the PR surface plainly discloses that no agent review ran on this
   increment (trust mode), so the human reviewer knows they are the first
   semantic reader.
4. **Given** a trust-mode task whose verify gate (tests) fails, **When** it
   settles, **Then** it fails exactly as today — no executable gate is
   weakened by this change.

---

### User Story 2 - Strict mode preserves today's reviewer byte-identically (Priority: P2)

As the operator dispatching a goal I will *not* human-review (the
full-autonomy north-star path), setting `strict` strictness gives me exactly
today's behavior: the adversarial diff review runs on every increment,
fail-closed, degradation ladder intact, crashes never approve.

**Why this priority**: The reviewer is being repositioned, not deleted. Strict
mode is the container that keeps the full-autonomy path honest and makes this
change reversible per-goal with an existing dial instead of a code change.

**Independent Test**: Run the existing review-gate test suite against a
strict-mode goal; every assertion that holds today must hold unchanged.

**Acceptance Scenarios**:

1. **Given** a strict-mode goal, **When** a task reaches the review stage,
   **Then** the adversarial diff review runs with today's exact semantics
   (findings block, crashes fail closed, degradation ladder engages on
   oversized input).
2. **Given** a strict-mode task whose reviewer crashes unreviewably, **When**
   it settles, **Then** it fails closed with no agent retry — byte-identical
   to today (#186).

---

### User Story 3 - The operator can see which chain governed each delivery (Priority: P3)

As the operator triaging a morning digest, every delivery and cycle report
tells me which gate chain governed it (trust chain vs strict chain), so I can
attribute outcomes correctly and the clean-cycle rate measures the mechanism
that actually ran.

**Why this priority**: Legibility guard — without it, a mixed fleet of trust
and strict goals makes wedge statistics uninterpretable. Valuable, but the
system is correct without it.

**Independent Test**: Deliver one trust-mode and one strict-mode task; the
delivery records and PR surfaces distinguish the two chains.

**Acceptance Scenarios**:

1. **Given** settled tasks from goals of both strictness values, **When** the
   operator reads the deliveries tail or the PR, **Then** each is labeled with
   the gate chain that governed it.

---

### Edge Cases

- **Strictness change mid-goal**: strictness is read at gate-evaluation time,
  so flipping a goal to `strict` applies to its next task settlement without
  re-dispatch. No task is governed by two chains at once.
- **In-flight goals at deploy time**: existing trust goals (e.g.
  `ledger-2026-08-12`) pick up the new chain on their next settlement — no
  migration, no retroactive re-review of already-delivered increments.
- **Done-gate is untouched**: the goal-level `review_repository` done-check
  keeps its LLM call and its always-hard semantics in both modes. Review-shaped
  cognition wedges drop to once-per-cycle with bounded input — not to zero —
  and a done-check crash still blocks completion (never approves it).
- **Test-integrity becomes the sole automated guard against test-weakening
  under trust.** It stays always-hard and untouched; a test-integrity crash
  still fails the task closed in both modes.
- **Quota/auth failures in remaining gates** still classify as pause-and-resume
  exactly as today; removing the reviewer removes call volume but changes no
  pause semantics.
- **A trust-mode increment that an agent reviewer would have caught** (e.g. an
  empty PLAN.md-only increment) now ships as a PR; the backstops are the
  done-gate (bounces the goal off_track at the next done proposal) and the
  human PR review. This is an accepted, explicit trade — record, don't hide.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Under `trust` strictness, task settlement MUST NOT consult the
  per-increment adversarial diff reviewer: the gate chain is verify →
  browser-E2E (when triggered) → test-integrity → delivery. The reviewer
  component MUST NOT be invoked at all (zero calls, zero tokens) on this path.
  *(Resolved with Denys, clarify 2026-08-14: **full skip**. No advisory
  annotations under trust — wedge class, token burn, and host-memory
  contention all go to zero; tests + E2E are the only automated verdicts and
  the human PR review is the semantic reader.)*
- **FR-002**: Under `strict` strictness, the per-increment adversarial diff
  review MUST behave byte-identically to today: findings block, infrastructure
  crashes fail the task closed with no agent retry, the oversized-input
  degradation ladder engages exactly as it does now.
- **FR-003**: The verify (tests), test-integrity, and goal-level done gates
  MUST remain always-hard in both strictness modes — no semantics change.
  The browser-E2E gate keeps today's ADR 0007 semantics untouched (mechanical
  no-run/crash fails closed; surviving findings advise-and-ship under trust).
  *(Resolved with Denys, clarify 2026-08-14: keep as-is; this spec changes one
  thing. Promoting E2E to always-hard is a candidate future spec.)*
- **FR-004**: Every trust-mode delivery MUST disclose on its PR surface that
  no per-increment agent review ran (loud, per constitution Principle VI), so
  the human merge review knows it is the first semantic reader.
- **FR-005**: Strictness MUST be read at gate-evaluation time and apply to all
  goals, including goals already in flight when the change deploys.
- **FR-006**: The constitution's Principle V and the corresponding CLAUDE.md
  hardening language MUST be amended in the same arc to state that under
  `trust` the per-increment diff review is not part of the gate chain, and
  that fail-closed-on-crash governs *consulted* gates. Shipping the behavior
  without the amendment (or vice versa) is a spec violation.
- **FR-007**: The change MUST ship with named regression tests, at minimum:
  a trust-mode task with an unconditionally-crashing reviewer stub settles
  `done` with zero reviewer invocations; a strict-mode goal exercises today's
  review-gate suite unchanged; all existing zero-token guard tests
  (`FakeClaude.calls == 0`) stay green.
- **FR-008**: The goal-level done-check (`review_repository` against the
  firmed `done_when`) MUST be untouched by this feature in both modes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Per-increment review-infrastructure wedges (process-kill /
  timeout / unparseable-verdict from the diff reviewer) on trust-mode nights
  go to **zero by construction** — the component is not invoked, so the
  failure class cannot occur. Verified over the next 5 active trust-mode
  nightly cycles: 0 wedges of this class.
- **SC-002**: Replaying the 2026-08-13 night's failure shape under the new
  chain, the task that died on the reviewer prose-crash (`b8f23f4f`) settles
  and delivers instead of failing — one fewer failed task per night of that
  shape, with the empty-increment catch deferred to the done-gate and the
  human PR review.
- **SC-003**: Strict-mode behavior is demonstrably unchanged: the pre-existing
  review-gate test suite passes without modification against strict-mode
  goals.
- **SC-004**: Per-increment cognition call volume on trust-mode nights drops
  by the full share previously spent on diff reviews (at least one large-input
  LLM call per delivered increment), reducing both quota burn and host-memory
  contention during the night window.
- **SC-005**: No consulted gate gets weaker: every gate that *does* run under
  either mode still fails the task closed on its own crash, and the full test
  suite count does not regress from the pre-change baseline.

## Assumptions

- Companion mode remains the primary use-mode: a human reviews every
  trust-mode PR before merge. If unattended trust-mode merging is ever
  enabled, the strictness default must be revisited first (that would be a new
  spec).
- The `trust`/`strict` strictness dial (ADR 0007) exists, is per-goal, and is
  the correct switch to condition on — no new dial is introduced.
- The done-gate's own large-input fragility (e.g. `review_repository` timeouts
  on big repos) is out of scope here; it is a separate, already-filed problem
  class and its cadence (once per cycle) bounds its blast radius.
- The test-integrity gate is currently always-hard and mechanically
  independent of the diff reviewer; this spec relies on that and does not
  modify it.
- Wedge/cycle accounting needs no special-casing for the reviewer once it is
  out of the trust chain; any residual accounting cleanup is P2 (below).

## Direction Memory — Rejected Alternatives

*(Recorded per `.claude/rules/speckit-workflow.md`: the spec is the direction
memory; a decision that exists only in conversation is the failure mode this
discipline prevents.)*

- **Rejected: `review-gate-resilience` (harden instead of reposition).** The
  worked-out alternative was: add a re-ask rung with a JSON-format nudge for
  unsplittable diffs where the degradation ladder cannot split (would have
  recovered the 2026-08-13 prose-crash), and stop counting ladder-recovered
  first-attempt timeouts as cycle wedges. Rejected because hardening a
  component that is redundant under the primary use-mode violates "good
  design is light" — you don't harden what you can remove from the hot path.
  The wedge-accounting concern self-resolves once the reviewer is out of
  trust mode. If strict-mode usage grows later, the re-ask rung is the first
  candidate increment to revisit (P3 below).
- **Rejected: deleting the adversarial reviewer outright.** Fresh-context
  agent review demonstrably catches worker self-deception (empty increments,
  spec-fidelity drift — both observed live this week). It stays where the
  claim is large and no human is awake: the done-gate always, and per-increment
  under `strict`.
- **Rejected: keeping the reviewer under trust with crashes downgraded to
  advisory.** This would repeal fail-closed-on-crash for a consulted gate —
  a direct Principle V violation with no clean reading. Not consulting the
  gate at all under trust is the honest version of the same intent.
- **Rejected (clarify 2026-08-14): fire-and-forget reviewer under trust for PR
  annotations.** Would have kept advisory findings on the PR while taking
  crashes off the failure path — but retains the full per-increment token
  burn, host-memory contention, and the ×117-fingerprint call volume for
  advisory-only output the human reviewer doesn't need. Denys chose full skip.
- **Deferred (clarify 2026-08-14): promoting browser-E2E to always-hard under
  trust.** Reads consistent with "tests then e2e as the gate", but it is a
  second behavior change with its own wedge-risk trade (E2E fail-closed has
  ×5 catalog entries); this spec changes one thing. Candidate future spec.

## Constitution Impact *(explicit, per Governance)*

This spec **requires an invariant amendment** and says so explicitly:

- **Principle V** currently reads "The trust dial recalibrates the two
  review-shaped gates; it never repeals fail-closed for crash/quota cases."
  The amendment: under `trust`, the per-increment adversarial diff review is
  **not part of the gate chain at all**; fail-closed-on-crash governs every
  gate that is *consulted*. #186's guarantee — nothing ships on a consulted
  gate's silence — is untouched; a gate that is by-policy not consulted
  produces no silence to ship on.
- **CLAUDE.md** ("Hardening philosophy" bullet on gate strictness) is amended
  in the same PR; CLAUDE.md remains canonical on conflict.

## Slicing

- **P1 (firm — 1 PR)**: the strictness-conditional gate chain, the PR-surface
  disclosure (FR-004), the constitution + CLAUDE.md amendment, and the named
  regression tests (FR-007). Ships as one behavior-change PR.
- **P2 (named, unsized)**: wedge/cycle-report accounting cleanup if any
  reviewer-shaped residue remains visible in trust-mode cycle reports after P1
  lands.
- **P3 (named, unsized)**: strict-mode reviewer resilience (the re-ask rung
  from the rejected alternative) — only if strict-mode usage grows enough for
  its wedge rate to matter on the scorecard.
