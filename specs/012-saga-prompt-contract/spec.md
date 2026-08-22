# Feature Specification: Saga & Unit-of-Work Prompt Contract

**Feature Branch**: `spec/012-saga-prompt-contract`

**Created**: 2026-08-22

**Status**: SPECIFIED, NOT IMPLEMENTED — merged 2026-08-22; no plan or tasks yet

**Relationship to spec 010**: the 2026-08-18 ruling has two halves. Spec 010
(`010-unit-of-work-parallelism`, drafted 2026-08-19, merged 2026-08-22) specifies
the **concurrency** half — single-writer per project, #553 closed by construction, and
the `[P]` fan-out machinery. This spec specifies the **input** half — the prompt
contract for a saga and for a unit of work. They share the terminology table and
nothing else; 010 is the normative source for everything about concurrency.

**Input**: Give a saga and a unit of work each a distinct, structured prompt, feed
delivery outcomes forward between increments, and record a work item's expected
extent — per the terminology ruled on 2026-08-18.

## Context: this formalises a settled decision

The vocabulary below was **ruled by the owner on 2026-08-18** and is adopted
verbatim. This spec does not reopen it. Spec 010 FR-007 requires the
same vocabulary be adopted into the architecture documentation; when that lands,
**that document becomes normative** and this table becomes a convenience copy —
keep them in step or delete this one. The owner's durable meta-preference is to
stand on established software-engineering paradigms and their canonical names,
never homegrown vocabulary implicitly re-derived.

| Concept | Canonical name | In devclaw |
|---|---|---|
| The ask | **Work item** (Kanban) | a GitHub issue |
| Admission to work | **Definition of Ready** | the `devclaw-ready` grade |
| Completion judgement | **Definition of Done** | the done-gate |
| The milestone-level objective | **Saga / long-running process** | a goal |
| The execution atom | **Unit of Work** (Fowler) | a task: one sandbox run → one atomic, verified, PR-able change-set |
| The plan | **Task graph (DAG)** | `tasks.md`; `[P]` marks topological independence |
| Parallel safety | **Hermetic action with declared I/O** (Bazel) | declared file scope + settle-time diff-scope check |
| Concurrency default | **Single-writer / actor-per-project** | at most one goal actively dispatching per project |
| Integration | **Merge queue** (Bors) | parallel execution, serial integration in queue order |

The last three rows — hermetic declared I/O, single-writer per project, and the
merge queue — are **specified by spec 010, not here**. They appear in this table
only so the vocabulary reads whole. Two consequences this spec inherits and does
not decide: parallelism is data in the plan and no worker ever spawns workers; and
spec directories are allocated at planning time, which closes #553 by construction.

## The problem

Stated by the owner on 2026-08-22: *"everything is so random… I want input to be
expected and output to be expected."*

The **output** side is already standardised and load-bearing. The worker returns a
fixed contract — `STATUS`, `CHANGED`, `VERIFIED`, `ACCEPTANCE`, `FOLLOW-UPS`,
`REPO NOTES` — which is parsed into structured task results, and the `REPO NOTES`
slot is what makes knowledge compound across runs. **This spec does not touch it.**

The **input** side has no equivalent:

1. **The two levels share one string.** A goal's objective and completion criteria
   are free prose. Each task under that goal receives that same prose with a fixed
   preamble prepended. There is no task-level authoring surface at all, so the
   goal/task separation the owner's model requires exists in execution but is
   erased at the prompt layer.
2. **Nothing is fed forward between increments.** The task graph records which
   increments are *planned* and which are *ticked*, but not what any of them
   actually *delivered* or how it was *judged*. Delivery outcomes are recorded and
   never read back.
3. **Work items carry readiness but not size.** Grading establishes that an ask is
   well-formed with verifiable completion intent. It says nothing about magnitude,
   so whether a graded work item becomes a saga or a single unit of work is the
   dispatcher's judgement, and two equivalent work items can take different shapes
   depending on who dispatched them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An increment knows what its siblings delivered (Priority: P1)

The operator starts a saga spanning several increments. The first increment ships.
When the second increment runs, its prompt states which increment this is and what
the previous ones delivered — including the change-set they produced and the
verdict each received — so it builds on that work instead of rediscovering or
duplicating it.

**Why this priority**: This is the smallest change that makes the two levels
distinct in practice, and it is the one with observed cost. Work has been
re-implemented that was already delivered, because nothing told the worker it
existed. It is also independently valuable: even if no other story ships, saga
increments stop repeating each other.

**Independent Test**: Run a saga through two increments and inspect the second
increment's prompt. It must name its position in the saga and describe the first
increment's delivered outcome. Fully testable without any change to how goals are
authored or how work items are graded.

**Acceptance Scenarios**:

1. **Given** a saga whose first increment has delivered a change-set and received a
   verdict, **When** the next increment is dispatched, **Then** its prompt states
   the prior increment's outcome and verdict.
2. **Given** a saga with no completed increments, **When** the first increment is
   dispatched, **Then** its prompt states that no prior increment exists, rather
   than omitting the section or asserting something false.
3. **Given** a prior increment that failed, **When** the next increment is
   dispatched, **Then** its prompt reports that failure and its reason, so the
   attempt is not repeated unchanged.

---

### User Story 2 - A saga is authored against a schema, not prose (Priority: P2)

Whoever creates a saga fills named slots — what is being achieved, what completion
means, what is deliberately excluded, which invariants must survive, and what is
already established and must not be re-derived — instead of composing free prose.
Two authors describing equivalent work produce equivalent sagas.

**Why this priority**: It removes the largest remaining source of variance, but it
is worth less than US1 until increments stop repeating each other, and it is the
change most likely to make prompts *worse* if slots are added that no reader acts
on. It should be designed against evidence from US1.

**Interlock with FR-009a**: because the saga framing is re-sent with every
increment, its size is multiplied by the increment count. That is affordable only
once this story replaces today's prose with compact slots. Today's behaviour is
already "re-send in full" — what is missing is the bound, not the mechanism.

**Independent Test**: Have two people author the same objective and compare the
resulting sagas slot by slot. Separately, attempt to create a saga with a required
slot missing and confirm it is rejected at creation with a specific message.

**Acceptance Scenarios**:

1. **Given** a saga description missing a required slot, **When** creation is
   attempted, **Then** it is rejected at creation time naming the missing slot,
   rather than accepted and discovered by a worker mid-run.
2. **Given** two equivalent objectives authored independently, **When** both sagas
   are created, **Then** their prompts have the same structure and differ only in
   content.

---

### User Story 3 - A work item carries its expected increment count (Priority: P3)

When a work item is graded ready, it records how many units of work it is expected
to take, along with the basis for that estimate. **Every work item becomes a saga
regardless of that number** (ruled 2026-08-22) — the count sizes the plan, it does
not select a different execution shape. A work item whose extent cannot be
estimated confidently is surfaced to a human rather than silently defaulted.

**Why this priority**: It stops a one-line fix being planned as a five-slice saga,
and a multi-increment body of work being planned as one. It is last because the
plan it sizes only becomes predictable once US1 and US2 land.

**Independent Test**: Grade one obviously multi-increment work item and one
obviously single-increment work item, and confirm each records an expected
increment count with a stated basis, and that both are executed as sagas.

**Acceptance Scenarios**:

1. **Given** a work item whose filer claimed one increment and whose content is a
   single atomic change, **When** it is graded, **Then** grading records agreement,
   **And** it is still executed as a saga subject to the completion judgement.
2. **Given** a work item whose filer claimed one increment but whose content spans
   several, **When** it is graded, **Then** the claim is preserved, the
   disagreement is recorded, and it is surfaced for a human rather than corrected
   silently.
3. **Given** an unchanged work item graded twice, **When** both grades complete,
   **Then** the recorded expected count is identical, because it is the filer's
   claim rather than a re-judged estimate.

---

### Edge Cases

- A saga's first increment has no predecessor — the absence must be stated
  explicitly, never omitted or fabricated.
- A prior increment delivered a change-set that was subsequently rejected or
  reverted — the next increment must not be told to build on it as though accepted.
- A prior increment's outcome is missing or unreadable — the increment proceeds
  with that gap stated, rather than failing or silently asserting there was none.
- A saga is resumed after a long pause and its prior outcomes are stale relative to
  the current state of the repository.
- The accumulated feed-forward grows without bound across a long saga, eventually
  costing more than the rediscovery it prevents.
- Two work items are graded the same size but one is genuinely decomposable and the
  other is not — size and decomposability are not the same axis.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A unit of work MUST receive a prompt distinct from its saga's prompt.
- **FR-002**: A unit of work's prompt MUST state its position within the saga.
- **FR-003**: A unit of work's prompt MUST state what previously completed units of
  work in the same saga delivered, including the outcome and the verdict each
  received.
- **FR-004**: When there are no previously completed units of work, the prompt MUST
  say so explicitly rather than omitting the information.
- **FR-005**: When a previous unit of work failed, its failure and reason MUST be
  stated so the attempt is not repeated unchanged.
- **FR-006**: The existing worker return contract MUST remain unchanged. This
  feature governs input only.
- **FR-007**: A saga MUST be authored from named slots covering, at minimum: what is
  being achieved, what completion means, what is excluded, which invariants must
  survive, and what is already established.
- **FR-008**: A saga missing a required slot MUST be rejected at creation time,
  naming the missing slot.
- **FR-009**: Every slot in either prompt MUST be justified by the behaviour it
  changes. A slot that does not change what a worker does MUST NOT be added.
- **FR-009a**: The saga framing MUST be re-sent in full with every unit of work.
  A prompt MUST NOT depend on the worker choosing to fetch framing from elsewhere
  (ruled 2026-08-22): each unit of work runs in a fresh sandbox with no memory of
  previous runs, so a pointer is a request while a slot is a fact.
- **FR-009b**: The saga framing MUST be bounded in size, so that "self-contained"
  cannot become "unbounded". Re-sending is affordable only because US2 makes the
  framing compact; re-sending unstructured prose on every increment is the failure
  mode this requirement exists to prevent.
- **FR-010**: A work item MUST carry an expected increment count supplied by its
  filer, together with the basis for that claim.
- **FR-010a**: Grading MUST validate the filer's claim against the work item's
  content and record whether it agrees.
- **FR-010b**: Grading MUST NOT silently overwrite the filer's claim. The claim
  stands as the record; a disagreement is recorded and surfaced for a human
  (ruled 2026-08-22).
- **FR-011**: A work item whose extent cannot be estimated confidently, or whose
  claimed count grading disagrees with, MUST be surfaced for a human decision
  rather than silently defaulted or silently corrected.
- **FR-012**: Every work item MUST execute as a saga regardless of its expected
  increment count. The expected count sizes the plan; it MUST NOT select a
  different execution shape.
- **FR-012b**: FR-012 governs **work items** — asks that pass through intake and
  grading. It does NOT govern goal-less direct dispatch, which spec 010 FR-009
  keeps as a legitimate operator-present path exempt from the project lock. This
  spec MUST NOT be read as removing that path.
- **FR-012a**: The completion judgement MUST NOT be bypassed for any work item,
  however small. Mechanical verification passing is never sufficient evidence of
  completion.
- **FR-013**: Any assessment requiring reasoning MUST occur at grading time. No new
  reasoning call may be added to the recurring background cycle.
- **FR-014**: Concurrency, declared file scopes and serial integration are
  specified by spec 010 and are OUT OF SCOPE here. This spec must not restate or
  contradict them.

### Key Entities

- **Work item** — an ask, with a readiness grade and (new) a recorded size and the
  basis for it.
- **Saga** — a milestone-level durable objective. Owns the durable framing that is
  stable across all of its units of work.
- **Unit of work** — one atomic, verified, deliverable change-set produced by one
  execution. Owns the framing specific to this increment.
- **Task graph** — the ordered plan of units of work, marking which are
  topologically independent and, where relevant, each one's declared file scope.
- **Delivery record** — what a completed unit of work produced and the verdict it
  received. Already captured; this feature reads it back.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An increment in a saga never repeats work a previous increment in the
  same saga already delivered.
- **SC-002**: Given a saga and its history, a reader can determine which increment
  is running and what preceded it, without inspecting the repository.
- **SC-003**: Two people authoring the same objective independently produce sagas
  with identical structure, differing only in content.
- **SC-004**: A malformed saga is rejected at the moment of creation, not
  discovered partway through an execution.
- **SC-005**: The same work item produces the same execution shape regardless of
  who dispatches it or when — and that shape is always a saga.
- **SC-005a**: A work item that is materially incomplete is never reported as done
  on the strength of its mechanical verification alone, at any size.
- **SC-005b**: Grading an unchanged work item twice yields the same expected
  increment count both times.
- **SC-006**: Total prompt size does not grow to the point of increasing execution
  failures — the standardisation must not cost more than the rediscovery it
  prevents. Because framing is re-sent per increment, this is measured across a
  whole saga, not a single prompt.
- **SC-007**: The idle cost of the system is unchanged: a system with nothing to do
  still consumes no reasoning calls.

## Assumptions

- The 2026-08-18 terminology is settled and is adopted verbatim. This spec does not
  re-derive or rename it.
- The worker return contract already works and is out of scope.
- The task graph already tracks increment position within a saga. A prompt slot that
  merely restates position would be redundant; the gap this spec closes is delivery
  *outcomes*, which are recorded but never read back.
- Delivery outcomes are already captured per delivery, so US1 requires no new
  storage and no new reasoning — only feeding existing data forward.
- **RULED 2026-08-22: everything remains a saga, whatever its size.** A work item
  records an *expected increment count*, never a saga-or-task verdict, and the
  completion judgement is never bypassed. Evidence: a single-unit work item passed
  its mechanical verification while being materially incomplete — the feature was
  unwired but nothing was deleted, justified by a gate that did not exist — and
  only the saga-level judgement caught it. Routing small work around that judgement
  would trade correctness for vocabulary tidiness, against the standing principle
  that "done" is a proposal.
- **RULED 2026-08-22: the saga framing is re-sent with every increment**, never
  referenced. A fresh sandbox has no memory, so referencing would depend on the
  worker following a pointer — and the stated goal is that input be *expected*,
  not hopeful. The size objection is an argument for making the framing compact
  (US2), not for making it fetchable.
- **RULED 2026-08-22: the filer claims the expected increment count; grading
  validates it and never silently overwrites it.** A grader-judged number is a
  reasoning output and would drift between identical re-grades, which is the
  opposite of predictable input; a filer-only number has no check at all. The
  claim is the stable record, disagreement is an explicit signal, and a human
  resolves it. No additional reasoning call is introduced — grading already reads
  the item.
- Size and decomposability are related but distinct axes. Something can be large
  and cleanly sliceable, or small and still require a completion judgement.
- The scaling story (declared parallelism, merge queue) lives in spec 010. It is
  meaningless until the single-increment path specified here is predictable, so 010
  depends on this spec landing first even though it was drafted earlier.

## Constitution Alignment

- **III. Zero-token idle** — FR-013 confines any new reasoning to grading time. The
  recurring background cycle gains no reasoning call, and SC-007 states the
  observable guarantee.
- **IV. Single writer to state** — this spec adds no new writer and no new
  concurrency: the single-writer-per-project default and the declared fan-out
  machinery are spec 010's territory (FR-014 keeps them out of scope here).
- **V. Verification fails closed; "done" is a proposal** — untouched. The
  Assumptions record why the completion judgement must not be bypassed for small
  work items.
- **VI. Loud failure over silent degradation** — FR-008 and FR-011 fail loudly
  rather than defaulting silently; FR-004 and FR-005 state absences and failures
  explicitly rather than omitting them.
- **VII. Fix the class, not the instance** — this spec exists because three
  separately-filed issues turned out to be one missing definition.

## Outstanding Clarifications

None — all three resolved with the owner on 2026-08-22; each ruling is recorded in
Assumptions with its evidence.

## Corollaries

Resolved by this spec rather than as separate arcs: **#600** (work items graded for
readiness but not size) and **#601** (saga and unit-of-work prompt schema).
**#553** (spec directory numbering collisions) is NOT closed by this spec — the
mechanisms that close it by construction (planning-time spec-directory allocation
and the single-writer-per-project default) are spec 010's, and 010 FR-006 owns the
closure.
