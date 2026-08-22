# Feature Specification: Unit of Work & Planned Parallelism

**Feature Branch**: `010-unit-of-work-parallelism`

**Created**: 2026-08-18

**Status**: P1 IN IMPLEMENTATION 2026-08-22 (plan + tasks in this directory).
P3 (`[P]` fan-out) remains SPECIFIED, NOT IMPLEMENTED — named-unsized.

**Amended 2026-08-22 (owner ruling, during planning)**: FR-005 and the
project-hold entity changed from a STORED, acquired/released lock to a
**DERIVED** holder. Rationale and the rejected original are recorded in
Rejected Alternatives; no other requirement changed.

**Implementation order**: depends on spec 012's US1 (increment feed-forward)
landing first — the single-increment path must be predictable before the
concurrency machinery matters (ruled 2026-08-22, recorded in spec 012's
Assumptions). #553 closes referencing THIS spec (FR-006), not 012.

**Input**: User description: "Encode the 2026-08-18 design ruling (posted on issue #553) into devclaw's dispatch layer: canonical unit-of-work terminology; DEFAULT single-writer per project (actor-per-project, zero-LLM invariant at the dispatch write-site); PLANNED parallelism as the earned exception (tasks.md [P] markers with declared file scopes, serial merge-queue integration, settle-time diff-scope enforcement); spec directory names allocated at planning time; closes #553; worker-spawned subagents explicitly rejected. P1 = the single-writer invariant + #553 closure; the [P] fan-out machinery = named-unsized P2/P3."

## Context & Motivation *(informative)*

Issue #553 surfaced as a numbering race: two goals running in parallel on one
project both created `specs/009-…` directories. The 2026-08-18 ruling reframed
it: the race is a symptom of **accidental parallelism on one project**, which
should not exist. Sandbox and worktree isolation already prevent *mechanical*
collisions; nothing can reconcile two *plans* that don't know about each other
— the failure is semantic drift that surfaces only at integration.

The design stands deliberately on established paradigms (ruled: canonical
software-engineering terms over homegrown vocabulary):

| Canonical term | devclaw referent |
|---|---|
| **Work item** (Kanban); **Definition of Ready** | the GitHub issue; the `devclaw-ready` grade (specs 006/009) |
| **Saga** (long-running process); **Definition of Done** | the goal; the done-gate |
| **Task graph / DAG** (build-system doctrine) | tasks.md; `[P]` marks topological independence — parallelism is *data in the plan*, never executor control flow |
| **Unit of Work** (Fowler) | the **increment**: one sandbox run → one atomic, verified, PR-able change-set — devclaw's execution atom |
| **Hermetic action with declared I/O** (Bazel) | a fan-out increment's declared file scope, enforced at settle |
| **Single-writer / actor-per-project** | the default concurrency mode this spec introduces |
| **Merge queue** (Bors "not rocket science" rule) | serial integration of concurrently-executed increments |

## Clarifications

### Session 2026-08-18

- Q: Does a BLOCKED holding goal keep or release the project lock? → A: **Keeps it.** Strict serialization: a blocked goal is waiting on the operator anyway — the operator resumes it or cancels it to free the project. Releasing while blocked would let a second goal plan against a repo whose unmerged spec directories are invisible on the holder's branch — re-opening the #553 collision class.
- Q: Does the lock cover goal-less direct companion dispatches (`dispatch_task` / `fix_bug` / `implement_feature`)? → A: **Exempt.** Companion tasks are operator-present and human-serialized by nature; the lock exists to stop *unattended* concurrent planners, and a direct dispatch IS the human judgment call. The dispatch response MUST warn loudly that a goal holds the project, then proceed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Single writer per project (Priority: P1)

At most one goal actively works a registered project at a time. When a second
goal targets the same project, it is accepted and **queued**: it does not
dispatch increments while the first goal holds the project. When the first
goal reaches a terminal state, the queued goal begins on the next heartbeat —
no operator action required. Goals on *different* projects are unaffected and
run concurrently exactly as today.

**Why this priority**: This is the ruling's default mode. It makes the #553
class (independent plans colliding on one repo) structurally impossible
instead of cleverly mitigated, and it matches the actual use pattern
(companion mode, one repo in focus per night).

**Independent Test**: Create two goals on one project and one goal on a second
project; verify only the first same-project goal and the other-project goal
dispatch; complete the first goal and verify the queued goal starts
automatically on a subsequent tick.

**Acceptance Scenarios**:

1. **Given** goal A is actively working project X, **When** goal B is created on project X, **Then** B is accepted, marked as waiting on the project, and dispatches nothing while A holds X.
2. **Given** A holds X and goal C exists on project Y, **When** the heartbeat runs, **Then** C dispatches normally — the lock is per-project, never global.
3. **Given** A reaches a terminal state (achieved / cancelled / failed), **When** the next heartbeat runs, **Then** B begins dispatching with no operator action.
4. **Given** B is waiting on X, **When** the operator inspects B, **Then** the wait is legible: which goal holds the project and why B is queued (loud, not silent).
5. **Given** any tick where B is waiting, **Then** the wait costs zero cognition (the hold check is a cheap local read — the zero-token idle guard extends to queued goals).

---

### User Story 2 - The #553 race is closed by construction (Priority: P1)

With single-writer as the default, no two planners run concurrently against
one project, so concurrent spec-directory allocation cannot occur. The
timestamp-prefix mitigation drafted on #553 is withdrawn as machinery for a
state the design forbids. #553 closes as this story's corollary.

**Why this priority**: The originating defect. Closing it by construction —
rather than by making the race survivable — is the ruling's point.

**Independent Test**: The regression that reproduced #553 (two goals on one
project reaching the spec-creation step) is re-run under this spec's default;
verify the second goal never reaches planning while the first holds the
project.

**Acceptance Scenarios**:

1. **Given** the single-writer default, **When** two goals target one project, **Then** their planning phases are strictly serialized — the second sees the first's merged spec directories before allocating its own.
2. **Given** this spec ships, **Then** #553 is closed referencing it, and no runtime numbering mitigation is introduced.

---

### User Story 3 - Planned fan-out on one project (Priority: P3 — named-unsized, do not build until P1 lands and earns it)

When a plan explicitly declares independent tasks (`[P]` markers in the task
graph, each with a declared file scope), the executor MAY run those increments
concurrently — but their integration onto the goal branch is strictly serial
(merge-queue order), and at settle time each increment's diff is verified to
lie inside its declared scope: a violation fails that increment loudly.
Parallelism is a property of the *plan*; a worker can never spawn workers.

**Why this priority**: The earned exception. It is only safe *because* P1
exists (fan-out happens inside one plan, under one goal, never as accidental
concurrent plans). Sized and built only after the single-writer default has
run in production.

**Independent Test** (when built): a plan with two `[P]` tasks with disjoint
scopes executes them concurrently, integrates serially, and a deliberately
out-of-scope edit in one increment fails that increment while the other lands.

**Acceptance Scenarios** (contract-level; detail at its own plan stage):

1. **Given** a task graph with `[P]` tasks carrying declared file scopes, **When** the executor fans out, **Then** integration onto the goal branch is serial, in queue order.
2. **Given** an increment whose diff touches paths outside its declared scope, **When** it settles, **Then** it fails loudly with the violation named — never ships on silence (mechanism, not prompt: workers route around soft constraints, #358).
3. **Given** fan-out is active, **Then** spec-directory names for any planned work were allocated at planning time by the task graph — never claimed at runtime.

---

### Edge Cases

- **The holding goal blocks (needs_human / mechanical block)**: it KEEPS the lock (clarify ruling). The operator unblocks the project by resuming or cancelling the holder; a queued goal's waiting reason names the blocked holder so the choice is legible.
- **Direct companion dispatches** (`dispatch_task` / `fix_bug` / `implement_feature` without a goal) on a locked project: EXEMPT (clarify ruling) — operator-present tasks proceed, with a loud warning in the dispatch response that a goal holds the project.
- **The holding goal is cancelled**: it becomes terminal, so it is no longer the computed holder; the next queued goal starts on the next tick. Nothing is "released" — the derivation simply returns a different answer.
- **Two goals created in the same instant on one project**: ordering follows the existing backlog convention (priority, then oldest), and ties break on a stable key (goal id) so the holder is deterministic rather than arrival-dependent. Under the derived hold there is no acquisition to race: both writers compute the same holder from the same rows.
- **Pre-existing state at upgrade**: if an instance already has two active goals on one project when this ships, neither is interrupted — the non-holder finishes settling whatever it already has in flight and simply dispatches nothing further, and the situation is surfaced loudly to the operator.
- **A queued goal is cancelled before ever starting**: it leaves the queue; no lock interaction.
- **One-shot vs long-lived**: both modes are goals and both respect the lock; mode changes cadence, never concurrency rights (ADR 0003's one-primitive rule).

## Requirements *(mandatory)*

### Functional Requirements — P1 (firm)

- **FR-001**: At most one goal per registered project may actively dispatch increments at any time (the single-writer default). Cross-project concurrency MUST be unaffected.
- **FR-002**: A goal created on a project whose lock is held MUST be accepted and queued, never rejected; its waiting state MUST be legible to the operator (which goal holds the project).
- **FR-003**: When the holding goal reaches a terminal state, the next queued goal (priority band, then oldest) MUST begin automatically on a subsequent heartbeat — no operator action, no cognition spent on the handover decision.
- **FR-004**: The lock check MUST be a cheap local read performed before any cognition or dispatch work; a tick where every same-project goal is queued MUST cost zero cognition (zero-token idle guard extended).
- **FR-005** *(amended 2026-08-22 — see Rejected Alternatives)*: The hold MUST be **derived**, not stored: the holder of a project is a pure function of existing goal rows — the first non-terminal goal on that project by priority band, then age. There is no acquire step, no release step, and no persisted holder field. Race-safety is therefore structural rather than enforced: concurrent readers (heartbeat and MCP tool paths) evaluating the same rows reach the same holder, so there is no race to lose and no stale hold to heal. Any future move to a stored hold MUST first say how a holder that dies mid-hold is detected and cleared.
- **FR-006**: No runtime spec-directory numbering mitigation is introduced; #553 MUST be closed referencing this spec once the default ships.
- **FR-007**: The canonical terminology (work item / saga / task graph / increment-as-Unit-of-Work) MUST be adopted in the repo's architecture documentation in the same arc, so the vocabulary is the documented contract, not conversation lore.
- **FR-008**: A blocked holding goal KEEPS the project lock; queued goals wait until it is resumed or cancelled. The queued goal's waiting reason MUST name the blocked holder.
- **FR-009**: Goal-less direct dispatches are EXEMPT from the lock; when the target project is locked, the dispatch response MUST carry a loud warning naming the holding goal, then proceed.

### Functional Requirements — P3 (named, unsized; firm at their own slice)

- **FR-101**: Concurrent execution of increments on one project is legal ONLY for tasks the plan marks topologically independent (`[P]`) with declared file scopes.
- **FR-102**: Concurrently-executed increments MUST integrate serially onto the goal branch in queue order.
- **FR-103**: At settle, an increment's diff MUST be verified against its declared scope; a violation fails that increment loudly (fail-closed, pure mechanism, zero LLM).
- **FR-104**: Spec-directory names for planned work MUST be allocated at planning time by the task graph, never claimed at runtime.
- **FR-105**: A worker MUST NOT spawn workers; the executor's concurrency degree is decided solely by the plan and the host's caps.

### Key Entities

- **Project hold** *(amended 2026-08-22)*: the single-writer hold on a registered project. **Derived, not stored** — the holder is computed on demand as the first non-terminal goal on the project by priority band, then age. It has no row, no acquisition timestamp, and no lifecycle: a goal holds its project exactly while it is non-terminal, and stops holding it the moment it goes terminal, because that is what the computation says. ("Lock" is retained loosely elsewhere in this spec as the everyday word for the same thing; the entity is the derivation.)
- **Queued goal**: an accepted goal that is not its project's holder; carries a legible waiting reason; ordered by priority band, then age. Queued gates DISPATCH only — a queued goal still settles work it already has in flight, so nothing is orphaned.
- **Increment (Unit of Work)**: unchanged — one sandbox run → one atomic, verified, PR-able change-set. This spec adds no new execution entity.
- **Declared scope** *(P3)*: the file-path set a `[P]` task claims; the contract the settle check enforces.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 concurrent actively-dispatching goals on any single project after P1 ships (measured across the regression suite and live shakedown).
- **SC-002**: The #553 reproduction scenario (two goals, one project, both reaching planning) cannot produce colliding spec directories in 100% of regression runs — because the second goal never plans while the first holds the project.
- **SC-003**: A queued goal starts within one heartbeat interval of the holding goal's terminal transition in 100% of regression cases, with zero operator actions.
- **SC-004**: Ticks where all same-project goals are queued spend zero cognition (the existing zero-token guard tests remain green, and a new named test asserts it for the queued path).
- **SC-005**: Cross-project throughput is unchanged: N goals on N distinct projects dispatch exactly as before this spec (no global serialization regression).
- **SC-006**: An operator inspecting a queued goal can identify the holding goal from the goal's own status surface in 100% of cases (no log-diving).

## Assumptions

- "Actively dispatching" is goal-scoped, not increment-scoped: the lock is held for the goal's active lifetime, not per sandbox run — serializing whole plans is the point (two interleaved plans on one repo would re-open the #553 class via branch isolation, even with serialized sandboxes).
- Queue-not-reject for the second goal: goals are durable intent (the no-field-patches doctrine); arrival order is preserved and visible rather than bounced back to the operator.
- The existing dispatch/concurrency caps and the run-window/pause machinery are unchanged; the project lock composes with them (a goal must pass window, pause, cap, AND lock).
- The 007 autonomous-claim path (unbuilt, flag OFF) will inherit this invariant for free when built, since it dispatches through the same path — no special-casing is introduced for it now.
- P3's fan-out applies within one goal's plan; it never relaxes the one-goal-per-project default.

## Out of Scope

- Building any part of the `[P]` fan-out machinery (scheduler, scope enforcement, merge queue) in P1 — named here, sized later, only after the default has production nights behind it.
- Changing sandbox isolation, delivery, gates, or the increment's definition — the Unit of Work is unchanged.
- Multi-instance coordination (two devclaw instances sharing one repo) — out of scope entirely; the lock is instance-local.
- Priority preemption (a P0 goal evicting an active holder) — a queued goal waits; preemption is a possible future slice, not assumed.

## Rejected Alternatives

- **Timestamp-prefixed spec directories** (the original #553 draft recommendation, withdrawn 2026-08-18): makes the numbering race survivable instead of impossible; solves the symptom while leaving accidental concurrent planning — the actual defect — in place. Machinery for a state the design forbids.
- **Union-numbering / host-reserved numbers / per-goal spec namespaces**: rejected in the #553 analysis (racy; host state; breaks the slice-guard's one-level contract, respectively).
- **Worker-spawned subagents for parallelism**: control-flow parallelism decided by the executor at runtime destroys hermeticity and per-unit accountability (no independent verification/delivery per fork). Parallelism must be plan data. This is build-system doctrine adopted deliberately.
- **Increment-scoped locking** (serialize sandboxes but let two goals interleave on one project): still allows two mutually-unaware plans on one repo — branch isolation means the second planner cannot see the first's unmerged spec directories; the #553 class survives. Goal-scoped locking is the only shape that kills it.
- **Rejecting the second goal at creation**: bounces durable intent back to the operator and loses arrival order; queueing preserves both and matches the existing backlog conventions.
- **A STORED project lock** (holder goal id + acquisition time, CAS'd on acquire, released on terminal state) — this spec's original shape, **rejected 2026-08-22 during planning**, FR-005 amended. It buys nothing the derived holder lacks and brings a failure class with it: a holder that dies, is force-cancelled, or is lost to a crash leaves a lock nobody releases, which then needs a timeout, a heal budget, or an operator unwedge verb — machinery for a state the derived form cannot enter. The holder is already fully determined by rows devclaw owns and CASes today (goal status + priority + age); persisting a second copy of a derived fact invites the two to disagree, and the disagreement is exactly the wedge. Deriving keeps the invariant enforceable in one pure function, satisfies FR-008 for free (a blocked goal is non-terminal, so it still holds), and adds no state to a layer whose single-writer discipline is already the thing under test. Consistent with the standing "good design is light" rule: heaviness in the design is the signal to reconsider, not to grind.

## Constitution Alignment

- **III (zero-token idle)**: the lock check is a cheap local read before any cognition; queued-goal ticks cost zero cognition (FR-004, SC-004).
- **IV (single writer to state)**: this spec *extends* the single-writer principle from state rows to project execution, and does so **without adding a writer**: the hold is derived from rows the CAS'd transition discipline already governs (FR-005, amended), so there is no second source of truth to keep in step.
- **V/VI (fail closed, loud over silent)**: P3's scope check fails increments loudly (FR-103); a queued goal's wait is legible (FR-002); upgrade edge cases surface loudly.
- **VII (fix the class)**: #553's numbering race is closed by removing its precondition, not by hardening the symptom.
