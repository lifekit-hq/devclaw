# Feature Specification: Ticket as Contract — the issue is the only holder of "what and why"

**Feature Branch**: `024-ticket-as-contract`

**Created**: 2026-08-27

**Status**: Draft — direction ratified by Denys 2026-08-27 (the one-lane arc, with spec 022); run `/speckit-clarify` before implementation.

**Input**: Ruled direction from the 2026-08-27 architecture session: devclaw's entity triple (issue / goal / task) is the canonical ticket / workflow / job shape — the smell is not the count but the **duplication of content** across them. The goal carries freeform `objective` + `done_when` prose restating the issue; the task carries `goal` prose restating both; the saga slots (`out_of_scope` / `invariants` / `established`) are demanded at goal-creation time as API arguments instead of living on the ticket. End state: for issue-backed work the **issue is the contract**; the goal is pure orchestration state; the task is pure execution state. Nothing invented — this is ordinary normalization: one home per fact, and the tracker is the home for intent.

## Why (the ruled frame)

Spec 019 made goals *point at* issues but still duplicated the ask into goal
prose; spec 022 makes the issue the identity. This spec finishes the
normalization: content that describes the work lives only on the ticket, so
editing the ticket is editing the contract, the done-gate judges against the
ticket, and authoring work stops being a form-filling ceremony (the saga-slot
rejection wall at `create_goal` — three required list arguments — moves into the
issue template where a human fills it once, in the ticket, where reviewers see
it).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Issue-backed goals carry no prose contract (Priority: P1)

When work is dispatched for an issue, the resulting goal stores no `objective` /
`done_when` prose of its own — the worker brief and the done-gate both draw the
ask and the completion criteria live from the issue. Editing the issue before the
next dispatch changes what the worker builds and what the gate judges, with no
goal mutation verb involved.

**Why this priority**: This is the normalization itself — it removes the second
(and third) home of the ask, which is the root of the "three entities" smell and
of every drift between what the issue says and what the goal believes.

**Independent Test**: Create an issue-backed goal; inspect the stored goal and
find orchestration state only. Edit the issue's acceptance criteria; observe the
next dispatch's worker brief and the next done-check judging against the edited
text.

**Acceptance Scenarios**:

1. **Given** an issue-backed dispatch, **When** the goal is created, **Then** the
   stored goal contains identity + orchestration state (mode, strictness, branch,
   phase, issue refs) and no duplicated ask/completion prose.
2. **Given** the issue's completion criteria are edited between increments,
   **Then** the next worker brief and done-check use the live edited text (spec
   019's live-fetch rule, now covering the whole contract).
3. **Given** the issue-less lane (bench/greenfield goals), **When** such a goal
   is created, **Then** prose `objective`/`done_when` still work exactly as today
   — this spec normalizes the issue-backed lane only.

---

### User Story 2 - Saga slots live in the issue template (Priority: P2)

`out_of_scope`, `invariants`, and `established` are authored in the issue (a
devclaw issue template carries the sections), read by grading as part of the
Definition of Ready, and consumed live by the worker brief — no longer required
arguments on the goal-creation call.

**Why this priority**: Moves the authoring friction to where the authoring
happens (the ticket), and makes the slots reviewable by anyone reading the issue
— today they exist only inside the goal store, invisible on the tracker.

**Independent Test**: File an issue using the template with the three sections;
grade it; dispatch it with no slot arguments; observe the worker brief carrying
the sections' content.

**Acceptance Scenarios**:

1. **Given** an issue with the template sections filled (empty sections count as
   explicitly declared empty), **When** it is dispatched, **Then** goal creation
   needs no slot arguments and the worker brief carries the sections verbatim.
2. **Given** an issue missing the sections entirely, **When** it is graded,
   **Then** the verdict treats the absent sections per the Definition of Ready
   (named in the gap comment), rather than rejecting at dispatch time.
3. **Given** existing goals created with API-passed slots, **When** this ships,
   **Then** they keep working unchanged (no migration of in-flight work).

---

### User Story 3 - One definition of done: the gate judges the ticket (Priority: P3)

The done-gate's contract source for issue-backed goals is the issue text alone
(acceptance criteria / done-when as written on the ticket), with the existing
ceremony-drop rule applied there. Closing the ticket remains the human act;
the gate proposes, the merge/close ratifies.

**Why this priority**: Completes "one home per fact" for the completion
criterion — the evaluator today decomposes a goal-stored `done_when`; after this
it decomposes the ticket's, so the reviewer on GitHub and the gate read the same
words.

**Independent Test**: Run a done-check on an issue-backed goal and verify the
evaluator's decomposition quotes the issue body, not a goal-stored copy.

**Acceptance Scenarios**:

1. **Given** an issue-backed goal proposing done, **When** the done-gate runs,
   **Then** its contract decomposition sources the live issue text and names any
   dropped ceremony clauses exactly as today.
2. **Given** an issue whose criteria were edited mid-goal, **When** the gate
   runs, **Then** it judges the edited criteria (and the log records which
   revision was judged).

---

### Edge Cases

- **Issue deleted or made inaccessible mid-goal**: load-bearing input rule
  (spec 019) — the goal blocks loud (`lost_ref`), never judges against emptiness.
- **Contract-weakening edits** (someone edits the issue to trivially pass the
  gate): the gate's rationale names the revision judged; the human merge/close
  remains the backstop — same trust model as today, now with the edit visible on
  the tracker's own history instead of hidden in goal state.
- **Conflicts between template sections and issue prose**: the template sections
  are the slots; free prose outside them is context. Grading names ambiguity as
  a gap rather than guessing.
- **The `spec` slot** (pre-aligned scope contract from scope_grill): remains
  supported; when present it complements the ticket rather than duplicating it —
  exact precedence is a clarify-phase question.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For issue-backed work, the ask and the completion criteria MUST
  have exactly one home: the issue. Goal storage MUST NOT carry a duplicated
  prose contract for such work.
- **FR-002**: Worker briefs and the done-gate MUST source the contract live from
  the issue at each use (never a creation-time copy), extending spec 019's
  live-fetch rule to the full contract.
- **FR-003**: A devclaw issue template MUST carry the saga sections
  (`out_of_scope`, `invariants`, `established`); grading MUST read them as part
  of the Definition of Ready; goal creation MUST NOT require them as arguments
  for issue-backed work.
- **FR-004**: The issue-less lane (bench/greenfield prose goals) MUST keep
  today's behavior byte-for-byte; this spec changes only the issue-backed lane.
- **FR-005**: The done-gate's log MUST record which issue revision it judged, so
  contract edits are auditable from the goal side.
- **FR-006**: Unreachable/deleted contract sources MUST block loud
  (`lost_ref`) — the existing load-bearing-input rule, restated here because
  after this spec the issue is the *only* contract.
- **FR-007**: If persisted goal-state shape changes, the change MUST ship its
  doctor check in the same arc (spec 016 FR-014).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An issue-backed goal's stored state contains zero words of
  duplicated ask/completion prose; the diff between "what the reviewer reads on
  GitHub" and "what the gate judges" is structurally empty.
- **SC-002**: Dispatching a templated issue requires zero slot arguments and
  zero form-filling beyond the ticket itself.
- **SC-003**: Editing an issue's criteria changes the next worker brief and the
  next done-check with no goal-mutation verb invoked.
- **SC-004**: The issue-less lane's existing tests pass unchanged.

## Assumptions

- Spec 022 (issue identity) and ideally spec 023 (issue-edit events re-trigger
  grading) land first; 024 is the content normalization on top.
- "Goals are durable, no field patches" (the ruled preference against
  `update_goal`) is *strengthened*, not violated: the contract becomes editable
  on the tracker precisely so the goal never needs a prose-mutation verb.
- The evaluator's ceremony-drop rule (`done_when` is repository behavior, never
  delivery ceremony) applies unchanged to ticket-sourced criteria.
