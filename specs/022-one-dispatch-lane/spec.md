# Feature Specification: One Dispatch Lane — issue-keyed companion dispatch over the goal primitive

**Feature Branch**: `022-one-dispatch-lane`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: "One execution lane: retire freeform direct dispatch, make dispatch_task sugar over a one_shot goal keyed to an issue. devclaw has two lanes for work with different guarantees; the direct-dispatch lane has no identity, no single-writer, no workspace prep. On 2026-08-27 this produced byte-identical PRs #715/#716 from two dispatches 9s apart, cross-contaminated increments from three concurrent dispatches sharing one workspace, and a context-overflow failure. Root cause: work can enter execution without an identity and outside the one primitive (ADR 0003)."

## The problem (incident-grounded)

devclaw has two lanes for mutating work, with different guarantees:

| Guarantee | Goal lane | Direct-dispatch lane |
|---|---|---|
| Work-item identity | goals reference graded issues (spec 019) | freeform prose, random task id |
| Duplicate protection | goal id + issue refs | none — every call makes a new task |
| Single-writer per project | enforced hold | **exempt** (spec 010 FR-009 — warns, runs anyway) |
| Workspace prep before run | every action | none (issue #491) |
| Dispatch caps / CAS transitions | yes | no |

Every pathology observed on 2026-08-27 lived in the second lane: two dispatches 9 seconds
apart for the same issue produced byte-identical PRs (#715/#716); three concurrent
dispatches sharing one workspace committed each other's in-progress files into each
other's PRs; a stale workspace ran a task against outdated state. The root cause is not
a missing dedup check — it is that **work can enter execution without an identity and
outside the one primitive** (ADR 0003: one primitive, one dial).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dispatching the same work twice yields one worker (Priority: P1)

Denys (or the OpenClaw waiter acting for him, including an MCP-timeout retry) asks
devclaw to work an issue. Seconds later the same ask arrives again — a retry, a
double-send, a second phrasing of the same request. Exactly one worker runs, exactly
one delivery (PR) results, and the second ask comes back with a receipt pointing at
the already-running work instead of silently spawning a twin.

**Why this priority**: This is the incident class that keeps recurring (duplicate PRs
"not the first time"). Identity-keyed admission kills it by construction, not by patch.

**Independent Test**: Dispatch the same issue twice in quick succession against a live
instance; observe one worker, one PR, and a second response that names the existing
work. A named regression test drives the same path stubbed.

**Acceptance Scenarios**:

1. **Given** an idle project and an open issue, **When** work for that issue is
   dispatched twice within seconds, **Then** exactly one execution starts and the
   second call returns a receipt identifying the existing work (observable via the
   instance's status surfaces and, live, by at most one PR appearing).
2. **Given** work for an issue is mid-run, **When** the same issue is dispatched
   again, **Then** no second execution starts and the response says the work is
   already in flight.
3. **Given** the identity store, **When** two creation attempts race, **Then** the
   uniqueness of (project, issue) is enforced by the store itself — a real constraint,
   not a read-then-write check (spec 012's nullable-`ref_id` lesson: a constraint that
   can be silently disabled is not a constraint).

---

### User Story 2 - Companion dispatches ride the full goal lane (Priority: P2)

When companion-mode work is dispatched, it gets every guarantee the goal lane already
provides: the single-writer project hold (no more exemption), workspace prepared to
the current default-branch head before the run, dispatch caps, and CAS'd state
transitions. Two companion asks against one project execute one-after-the-other, each
in a clean workspace — never interleaved in a shared checkout.

**Why this priority**: Serialization + prep eliminates the cross-contamination class
(foreign files committed into unrelated PRs) and the stale-workspace class (#491) in
one move. Depends on US1's shape (the ask becomes a one_shot goal, so the goal lane's
machinery applies automatically rather than being reimplemented).

**Independent Test**: Dispatch two different issues against the same project
concurrently; observe serialized execution, each run starting from a fresh
default-branch checkout, and two PRs each containing only its own change.

**Acceptance Scenarios**:

1. **Given** two open issues on one project, **When** both are dispatched at the same
   moment, **Then** the second execution does not start until the first settles, and
   each delivery contains only its own issue's changes.
2. **Given** a workspace left on a stale feature branch by a prior run, **When** a
   companion dispatch runs, **Then** the work starts from the repository's current
   default-branch head (observable: the delivered PR's base is current, not stale).
3. **Given** the project's dispatch cap is reached, **When** a companion dispatch
   arrives, **Then** it is held with the same legible cap message the goal lane gives.

---

### User Story 3 - The freeform-prose path is retired; every ask names an issue (Priority: P3)

Work enters execution only by naming an issue — the same doorway everything else
already uses (file_intake → issue = receipt; spec 019 goals-as-pointers). A prose-only
ask is turned away with an actionable receipt path, and any extra prose accompanying
an issue ref travels as context/steering, never as the work's identity.

**Why this priority**: This is the demolition step (relocation, not deletion) that
makes US1's identity guarantee total — without it, the identity-less side door stays
open and the class can recur through it. It lands last because the waiter and any
operator habits need the US1/US2 surface to exist first.

**Independent Test**: Attempt a prose-only dispatch; observe rejection with a message
naming the intake doorway. Dispatch with an issue ref plus prose; observe the prose
reaching the worker as context while identity/dedup key on the issue.

**Acceptance Scenarios**:

1. **Given** the cutover has landed, **When** a prose-only mutating dispatch arrives,
   **Then** an intake issue is auto-filed as the receipt and the work proceeds keyed
   to that fresh issue in the same call — one step for the caller, an issue identity
   in the store, no identity-less execution.
2. **Given** an ask naming an issue plus extra prose, **When** it is dispatched,
   **Then** the identity (and dedup) keys on the issue while the prose reaches the
   worker as steering/context.
3. **Given** the retirement, **When** the read-only kinds (`review_repository`,
   `validate_product`) are invoked, **Then** they behave exactly as before — out of
   scope, no issue required (they change nothing and carry no duplication risk).

---

### Edge Cases

- **Issue unreachable or closed at dispatch time**: the issue is a load-bearing input
  (spec 019 class rule), not best-effort grounding — the dispatch fails loud with an
  actionable message; no worker burns a session against an empty ask.
- **The same issue is already referenced by an existing long-lived goal**: the
  dispatch is **rejected, naming the goal**, and the rejection message carries the
  exact `steer_goal` invocation that would prioritize the issue inside that goal.
  Steering stays a deliberate human verb — a dispatch must never silently mutate a
  long-lived goal's direction. No operator override exists: an override would re-open
  the two-writers-on-one-issue window this spec closes. (Clarified 2026-08-27.)
- **Re-dispatch after completion**: allowed **iff the issue is open on the tracker**
  at dispatch time — the tracker stays the source of truth; closing the issue retires
  the identity, reopening it re-arms it. A dispatch for a closed issue is rejected
  with the issue's state named. The in-flight dedup (US1) covers the retry window
  while work runs; this rule governs only the after-completion case. (Clarified
  2026-08-27.)
- **Prose-only asks at cutover**: the system **auto-files the intake issue (receipt)
  and proceeds in one call** — the ask routes through the existing intake doorway
  (file_intake → issue), then dispatches keyed to the fresh issue. Companion one-step
  UX is preserved, every ask leaves a durable receipt, and the freeform surface is
  retired in the same arc with no warn-and-run window (prose still works; it simply
  acquires identity on the way in). (Clarified 2026-08-27.)
- **Waiter retry after an MCP timeout**: the first call may have succeeded without the
  waiter seeing the response; the retry must land on the existing work (this is US1
  scenario 1 — named here because the timeout-retry is the known trigger).
- **Two different projects, same upstream issue**: identity is per (project, issue);
  the same issue dispatched against two registered projects is two work items by
  design.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every mutating companion dispatch MUST name an issue as the work item;
  the issue is the identity the system keys admission on.
- **FR-002**: Dispatching an issue MUST create-or-attach: if no active work exists for
  (project, issue), a one_shot goal keyed to that issue is created and executed; if
  active work exists, the call returns a receipt identifying it and starts nothing.
- **FR-003**: The (project, issue) identity MUST be enforced by a real uniqueness
  constraint in the store — one that cannot be silently disabled by an absent value
  (spec 012 lesson) — so racing creation attempts cannot both win.
- **FR-004**: Companion work MUST receive the goal lane's guarantees with no
  companion-specific exemption: single-writer project hold, workspace prepared to the
  current default-branch head before each run, dispatch caps, CAS'd transitions, and
  the standard delivery path. Spec 010 FR-009's exemption is repealed by this spec.
- **FR-005**: The issue reference MUST be treated as a load-bearing input: fetched
  live at dispatch; unreachable, empty, or deleted refs fail loud and blocking with an
  actionable message — never a silent dispatch against an empty contract.
- **FR-006**: The dispatch response MUST distinguish "created new work" from "attached
  to existing work", and the goal's log MUST record deduplicated attempts — a swallowed
  duplicate is otherwise invisible to the operator.
- **FR-007**: Companion cadence MUST be preserved: a dispatch admitted when the
  project is free starts its worker as immediately as today's direct path (one_shot is
  the immediate cadence; no waiting on a periodic tick).
- **FR-008**: The kind-specific verbs (`implement_feature`, `fix_bug`) MUST survive as
  thin sugar over the one lane; the read-only kinds (`review_repository`,
  `validate_product`) MUST be byte-unaffected.
- **FR-009**: If persisted state shape changes (the issue-keyed identity), the change
  MUST ship its doctor check in the same arc (spec 016 FR-014) so a deployed instance
  can verify its own store.
- **FR-010**: The freeform-prose entry MUST be retired at cutover with no
  deprecation window: a prose-only mutating ask auto-files its intake issue
  (receipt) and proceeds keyed to it in the same call; no mutating work item can
  enter execution without an issue identity.
- **FR-011**: A dispatch naming an issue already in a long-lived goal's scope MUST
  be rejected with a message naming that goal and the exact steering invocation
  that would prioritize the issue there; no override path exists.
- **FR-012**: Re-dispatch of a completed identity MUST be admitted iff the issue is
  open on the tracker at dispatch time, and rejected naming the issue's state
  otherwise.

## Clarifications

### Session 2026-08-27

- Q: Companion dispatch names an issue already inside a long-lived goal's scope —
  attach, reject, or override? → A: **Reject, naming the goal**, with the exact
  `steer_goal` invocation in the message; steering stays a deliberate human verb;
  no override flag.
- Q: Is a completed (project, issue) identity re-dispatchable? → A: **Yes, iff the
  issue is open on the tracker** at dispatch time; closed issue ⇒ rejected naming
  its state; the tracker is the source of truth.
- Q: Prose-only asks at cutover — hard-reject or auto-file? → A: **Auto-file the
  intake issue and proceed in one call**; freeform surface retired in the same arc,
  no warn-and-run window.

### Key Entities

- **Work item**: an issue on the project's tracker — the unit of ask, the identity,
  and the idempotency key. Already the currency of intake (receipt) and of goal
  scoping (spec 019); this spec makes it the currency of companion dispatch too.
- **One_shot goal keyed to an issue**: the execution wrapper a companion dispatch
  creates or attaches to; carries the (project, issue) identity under a store-level
  uniqueness guarantee, and inherits the full goal-lane machinery.
- **Dispatch receipt**: what the caller gets back — either "created: <ref>" or
  "already active: <ref>" — the observable difference between new work and dedup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Dispatching the same issue N times while work is active yields exactly 1
  execution and exactly 1 delivery, for any N and any inter-call spacing — verified by
  a named regression test and observed live (the #715/#716 class produces one PR).
- **SC-002**: Zero cross-contaminated deliveries: a delivered PR for issue A contains
  no files originating from concurrently-asked issue B — the 2026-08-27 contamination
  class has no mechanism left (serialized, prepped workspaces).
- **SC-003**: After cutover, 100% of mutating work items in the store carry an issue
  identity; zero identity-less tasks are created.
- **SC-004**: A dispatch admitted on a free project starts executing within the same
  admission pass as today's direct path (no added human-perceivable latency in
  companion mode).
- **SC-005**: The operator can tell from the dispatch response alone whether work was
  created or already existed, without consulting logs.

## Assumptions

- The OpenClaw waiter can be updated in the same arc to pass issue references (it
  already reads issues for grading/backlog flows); waiter changes are config/prompt
  work outside this repo's scope but sequenced with US3's cutover.
- The intake doorway (file_intake → issue) remains the receipt path for new prose
  asks; this spec adds no second doorway.
- Long-lived goals continue to reference multiple issues (spec 019); this spec only
  governs how *companion* asks acquire identity — subject to the open clarification on
  collisions with long-lived goals' scopes.
- No constitution amendment is required: this spec removes an exemption and extends
  ADR 0003's "one primitive" to the last surface that bypassed it; it strengthens
  existing invariants (single-writer, zero-token idle guard untouched).
- Read-only kinds stay identity-free by design; they change nothing and carry no
  duplication or contamination risk.

## Direction: the end-state this spec starts (ruled with Denys, 2026-08-27)

devclaw's shape maps onto established practice, and the direction is to finish
that mapping rather than keep homegrown variants — nothing invented:

- **The entity triple is ticket / workflow / job** (issue / goal / task). Three
  entities is the canonical count (Temporal-style durable workflow over a job
  queue, driven by a tracker ticket); the smell is *content duplication* across
  them, not the count. This spec makes the ticket the identity; the follow-on
  end-state is that for issue-backed work the goal carries **no prose contract at
  all** — the issue is the contract, the goal is pure orchestration state, the
  task is pure execution state (planned as spec 024, "ticket-as-contract";
  saga slots migrate into the issue template).
- **Grading is Definition of Ready** (Kanban's Ready-column gate). Its awkwardness
  today is that refinement has no home and no trigger: the verdict lives in a tool
  response and nothing re-grades until a human calls a verb. The end-state is
  event-driven refinement **on the ticket** — grade on issue-opened/edited, post
  the missing elements as an issue comment, carry state as the label (planned as
  part of spec 023, "event-driven triggers").
- **Triggering becomes event-driven; the heartbeat demotes to a fallback timer.**
  The store is already event-sourced (append-only StateStore + projections); only
  the triggering is polled. GitHub webhooks (issue opened/labeled/edited, PR
  merged, check_run completed, deploy completed) drive the state machine; the
  heartbeat catches missed events and scheduled work. Fully compatible with the
  zero-token idle invariant — webhooks are mechanism, cognition still fires only
  at decision points (spec 023).
- **One sentence**: devclaw is an autonomous **Kanban pull system over
  GitHub-native events** — tickets refined to a Definition of Ready, pulled by a
  durable workflow engine under WIP limits (single-writer + dispatch caps ARE the
  WIP limits), executed as jobs, verified by gates, done when the ticket closes.
- **The DAG/planned-parallelism machinery is removed as dead weight, idea
  preserved.** The program-DAG remnants and the `[P]` fan-out lane (spec 010 US3,
  flag-off) go — one-worker-per-project is the ruled default and the 2026-08-27
  incident showed concurrency without identity is the live failure mode. The idea
  (declared fan-out over hermetic scopes with a merge queue, parallelism as plan
  data never executor control flow) is parked, not rejected — revisit when
  single-lane throughput measurably bottlenecks *after* this spec's identity
  model is live and trusted. Removal of the dead code rides this spec's US3
  demolition scope.

### Demolition scope: tests that die with the code (symmetric ratchet, ruled 2026-08-27)

Per the symmetric-ratchet rule, the demolition names its test casualties up
front — the implementing PRs delete these alongside the behavior they pin
(exact survivorship decided at plan time; a file listed here may keep
individual tests that pin retained behavior, e.g. `cancel`'s task-level
verbs):

- **Program/DAG machinery**: `tests/test_program_plan.py`,
  `tests/test_queue_dag.py`, `tests/test_start_program_alias.py`,
  `tests/test_cancel_program_guard.py`, `tests/test_fanout_plan.py`,
  `tests/test_fanout_integration.py`, and the program/fanout cases inside
  `tests/test_goal_tick.py`, `tests/test_cancel.py`, `tests/test_goal_engine.py`,
  `tests/goal_fakes.py`.
- **Freeform direct dispatch**: the prose-path admission cases in
  `tests/test_dispatch_task.py` and the single-writer-exemption warning cases
  around `_project_hold_warning` (`tests/test_task_parent_goal_id.py`,
  `tests/test_scope_gate.py`) — replaced by named regressions for the new
  create-or-attach admission, the repealed exemption, and the auto-filed
  receipt path.

This spec's implementation is expected to land **net-negative in both the code
and test columns** — the first since the 008 shrink.

## Rejected alternatives (direction memory)

- **Dedup/idempotency key patched onto the existing direct-dispatch lane** — rejected
  by Denys 2026-08-27: treats the symptom (duplicate tasks) while leaving the
  identity-less side lane, the single-writer exemption, and the unprepped shared
  workspace in place; the class would recur through the remaining gaps.
- **Serializing the direct lane without issue identity** — kills contamination but not
  duplicates (two identical asks would still queue and both run); identity is the
  load-bearing half.
- **UNIQUE on a nullable reference column as the sole guard** — explicitly ruled out;
  spec 012 demonstrated a nullable ref silently disables its own constraint.
