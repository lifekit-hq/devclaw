# Feature Specification: Structured problem resolution

**Feature Branch**: `031-problem-resolution`

**Created**: 2026-09-02

**Status**: Clarified 2026-09-02 (3 questions walked with Denys, all recommended options taken) — ready for `/speckit-plan`

**Input**: User description: "A goal that cannot proceed states its problem as a
typed thing, and the owner resolves it with one of exactly two typed moves; most
problems never reach the owner because they are refused at authoring."

## Why (the incident class)

The operating principle, ruled by Denys 2026-09-02: **"if something needs to be
answered, devclaw should say what the problem is, and we correct the
implementation or take an action."** Never free-text steering into an inbox.

Today a goal that cannot proceed writes prose into `blocked_on`, and the only
answer is `steer_goal`, which appends prose to the inbox. The question and the
answer are two unlinked strings in two places. Nothing records whether a
question was *answered* or the goal was *redirected*; the next worker cannot
tell the difference; the done-gate re-derives what the owner already settled
and asks again. The one structured question/answer exchange devclaw has
(`answer_unknowns`, firming phase) is disabled in production.

Measured on 2026-09-02 — eight owner pings in one day:

| cause | count | avoidable? |
|---|---|---|
| Requirement authored wrong — a clause needing a credential the sandbox cannot have (`issue-414`); "all tests pass" with no baseline, held hostage by one unrelated pre-existing failure (`issue-443`); an undecided design choice surfaced to a worker four hours in (`devclaw-030` clause 10); an acceptance criterion the worker could not meet twice (`fs-538`) | 4 | at authoring |
| The churn brake parked converging work | 3 | fixed (#802) |
| Genuinely needed the owner's judgement | ~1 | no |

Seven of eight did not need judgement. They needed a contract that could not
have been written that way, and a loop that states a problem in a form the
owner can answer in one move. The gate was *right* every time; the shape of
the conversation was wrong every time.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A blocked goal states a typed Problem (Priority: P1)

When the loop cannot proceed for a reason only the owner can settle, the goal
carries a **Problem**: what is wrong, which `done_when` clause it concerns, why
the loop cannot decide it itself, a bounded set of options, a default, and a
timebox. The owner sees one legible object — not a paragraph — wherever they
look (the owner ping, the console, the MCP read tools).

**Why this priority**: every other story consumes a Problem. Without a typed
statement there is nothing to answer with a typed move.

**Independent test**: drive the done-gate to a `needs_human` verdict on a
seeded goal; assert the goal's block carries a Problem with all six fields,
that the owner ping names the Problem's clause and options, and that the
console renders the same object.

**Acceptance Scenarios**:

1. **Given** a goal at the done-gate whose evaluator returns `needs_human`
   with a question, **When** the tick settles, **Then** the goal is blocked
   with a Problem whose clause is the unsatisfied `done_when` clause, whose
   options include the evaluator's proposed resolutions, and whose default and
   timebox are set.
2. **Given** a worker that honestly reports it cannot finish (a missing
   capability, impossible instructions), **When** the task settles, **Then**
   the goal carries a Problem with the worker's reason as *what is wrong* and
   the fixed option set *supply the capability / correct the requirement /
   cancel*.
3. **Given** the churn brake parks a goal, **When** the park lands, **Then**
   the Problem's clause is the one the latest verdict found unsatisfied, and
   the options are *correct the implementation / accept and close / split
   into a follow-up*.
4. **Given** a blocked goal with a Problem, **When** the owner reads it over
   the MCP surface, **Then** every field is present and machine-readable, and
   the legacy prose `blocked_on` still carries a one-line summary for
   readers that predate this spec.

---

### User Story 2 - The owner resolves with one of two typed moves (Priority: P1)

Two verbs, and only two, resolve a Problem: **correct the implementation**
(the requirement was right, the work was wrong — re-dispatch with the
correction recorded against the clause) and **decide** (the owner picks an
option or writes a decision, and it becomes part of the contract as a
devclaw-controlled fact). A Problem whose timebox expires with no answer takes
its default and *informs* the owner instead of parking.

**Why this priority**: this is the principle itself made first-class. It ships
with US1 as one increment — a Problem nobody can answer in a typed way is the
current failure mode with better formatting.

**Independent test**: seed a goal with an open Problem; call each verb over
the MCP surface; assert the goal unblocks, that a Decision row exists naming
the Problem and the clause, that the next dispatch's brief carries it as fact,
and that the inbox received **no** steering line.

**Acceptance Scenarios**:

1. **Given** an open Problem, **When** the owner calls *correct the
   implementation* with a correction, **Then** the goal returns to idle, the
   correction is recorded as a Decision attached to the Problem's clause, the
   dispatch budget is restored as for any human vouch, and the next brief
   states the correction as settled fact.
2. **Given** an open Problem, **When** the owner calls *decide* choosing an
   option, **Then** the Decision is recorded with the chosen option and the
   Problem is closed; the done-gate's next evaluation treats that clause as
   resolved by owner decision and does not re-litigate it.
3. **Given** an open Problem whose timebox has elapsed, **When** the next tick
   runs, **Then** the default option is applied as a Decision marked
   *defaulted*, the goal continues, and the owner receives one notice they
   can override with a later *decide*.
4. **Given** a goal with an open Problem, **When** the owner calls
   `steer_goal`, **Then** the call is refused and the response carries the
   current Problem and names the two resolution verbs; the goal's state is
   unchanged. A genuine direction change first resolves the Problem
   (typically *decide → split into a follow-up* or *cancel*), then steers.
5. **Given** an open Problem whose default option would close the goal,
   **When** its timebox elapses, **Then** the outcome rides the goal's
   strictness dial: under `trust` the defaulted Decision closes the goal
   through the existing done-gate path (merge-on-close proceeds, the accepted
   gap is recorded as a follow-up on the close, the owner is notified once);
   under `strict` the timeout parks the goal with the Problem still open and
   the owner is notified that only an explicit *decide* can close it.

---

### User Story 3 - Defective contracts are refused at authoring (Priority: P2)

At goal creation the `done_when` contract is linted against the classes that
produced the avoidable pings: a clause that names a capability the sandbox
cannot have; an absolute repository-wide predicate with no baseline; a clause
containing an undecided design choice. Each is refused or rewritten **now**,
to the author, instead of surfacing to a worker hours later.

**Why this priority**: it removes the largest share of pings (4 of 8), but it
is independent of the resolution flow and useful without it.

**Independent test**: submit each of the three defective clause shapes at
creation; assert each is caught with a named reason; submit the corrected
forms; assert admission.

**Acceptance Scenarios**:

1. **Given** a `done_when` clause that requires a capability the sandbox
   cannot provide (a credential, an external service, a human confirming),
   **When** the goal is created, **Then** creation is refused; the refusal
   names the clause and the capability it needs, and nothing is persisted.
   The author rewrites the clause as observable repository behaviour and
   resubmits. The contract is never changed on the author's behalf.
2. **Given** a clause of the form "all tests pass" with no baseline, **When**
   the goal is created, **Then** the clause is rewritten as "no new failures
   relative to the default branch", the rewrite is recorded as a Decision,
   and the author is told.
3. **Given** a clause whose satisfaction depends on a design choice the
   contract does not make, **When** the goal is created, **Then** the choice
   is surfaced to the author as a Problem *before* any dispatch, with options
   and a default.
4. **Given** a contract with none of the three defects, **When** the goal is
   created, **Then** admission is unchanged from today.

---

### User Story 4 - Decisions are fed forward as fact (Priority: P2)

Every recorded Decision — from a typed move, a default, or the admission lint —
reaches the next worker and the done-gate through the same channel prior
increments use: devclaw-controlled facts, never worker prose. Neither
re-derives what the owner settled; neither asks again.

**Why this priority**: without it, a Decision is a row nobody reads and the
done-gate re-opens the same question next round.

**Independent test**: record a Decision on a clause; dispatch; assert the
brief carries it under the feed-forward marker; run the done-gate; assert the
verdict cites the Decision for that clause instead of re-evaluating it.

**Acceptance Scenarios**:

1. **Given** a goal with one or more Decisions, **When** the next action is
   dispatched, **Then** the brief carries each Decision (clause, choice, who,
   when) as settled fact, bounded like the prior-increments section.
2. **Given** a Decision on a clause, **When** the done-gate evaluates, **Then**
   that clause's verdict is *resolved by decision* with the Decision cited as
   evidence, and the aggregate verdict treats it as satisfied.
3. **Given** a later Decision on the same clause, **When** it is recorded,
   **Then** it supersedes the earlier one; the history is kept; only the
   latest is fed forward.

---

### Edge Cases

- A Problem raised while another is already open on the same goal: the newer
  one is recorded and linked; the goal shows one *current* Problem.
- The owner answers a Problem that the loop already defaulted: the explicit
  Decision supersedes the defaulted one; if the default already advanced the
  goal, the next dispatch carries the override.
- The evaluator returns `needs_human` with no usable options: the Problem is
  raised with the fixed fallback option set and the default *correct the
  implementation*; the loop never invents options it cannot justify.
- A worker honest-block that names a capability the admission lint should have
  caught: the Problem is raised AND the lint's miss is recorded as a problem
  in the catalog, so the class is closed, not the instance.
- A Decision references a clause that a later `steer_goal` removed from the
  contract: the Decision is retained as history and no longer fed forward.
- The timebox elapses while the instance is paused on a usage limit: the
  default is applied at the first tick after resume, never during the pause
  (zero-token idle holds).
- Mechanical blocks (`mechanical:*`) raise no Problem — they self-heal today
  and keep doing so; only human-gated kinds carry one.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A goal blocked for a human-gated reason (`needs_answer`,
  `donegate_churn`, a worker honest-block) MUST carry exactly one current
  Problem with: what is wrong; the `done_when` clause concerned (or *none* for
  a contract-level problem); why the loop cannot decide it; two to five
  options; a default option; a timebox.
- **FR-002**: The Problem MUST be readable over the MCP surface as a
  structured object and MUST be rendered by the console; the owner ping MUST
  name the clause and the options. `blocked_on` MUST still carry a one-line
  summary.
- **FR-003**: Exactly two resolution verbs MUST exist for a Problem — *correct
  the implementation* and *decide* — each recording a Decision (problem,
  clause, choice or correction text, who, when, provenance: owner /
  defaulted / admission) and unblocking the goal through the existing CAS'd
  transition. Neither MUST write to the steering inbox.
- **FR-004**: A human resolution MUST restore the goal's dispatch and churn
  budgets exactly as `steer_goal` / `resume_goal` do today.
- **FR-005**: When a Problem's timebox elapses, the next tick MUST apply the
  default as a Decision with provenance *defaulted*, continue the goal, and
  notify the owner once. A defaulted Decision MUST be overridable by a later
  explicit one.
- **FR-006**: `steer_goal` on a goal with an open Problem MUST be refused;
  the refusal MUST return the current Problem and name the two resolution
  verbs, and MUST leave the goal's state unchanged. Steering is accepted
  only when no Problem is open. (Ruled 2026-09-02, Q1: option A — the
  principle is enforced structurally; there is no escape hatch into prose.)
- **FR-007**: A defaulted Decision whose option is *accept and close* MUST
  ride the goal's strictness dial: under `trust` it closes the goal only
  through the existing done-gate ACHIEVE path (merge-on-close proceeds, the
  accepted gap is recorded as a follow-up on the close, one owner notice);
  under `strict` the timeout MUST park the goal with the Problem open and
  notify that only an explicit *decide* can close it. (Ruled 2026-09-02,
  Q2: option C — the same class as advise-and-ship vs hold, constitution V.)
- **FR-008**: The `done_when` admission lint MUST run at goal creation and
  MUST catch, with a named reason each: (a) a clause requiring a capability
  the sandbox cannot provide; (b) an absolute repository-wide predicate with
  no baseline; (c) a clause containing an undecided design choice. A contract
  with none of these MUST be admitted unchanged.
- **FR-009**: For class (a) the lint MUST refuse creation, naming the clause
  and the capability it requires, persisting nothing; the contract is never
  rewritten on the author's behalf. Class (b) MUST be rewritten to "no new
  failures relative to the default branch" and recorded as an admission
  Decision (the rewrite is unambiguous). Class (c) MUST raise a Problem to
  the author before any dispatch (it is a choice, so it gets options).
  (Ruled 2026-09-02, Q3: option A — the author stays the author; a guessed
  rewrite would become a settled fact the gate then enforces.)
- **FR-010**: Every Decision MUST ride the prior-increments feed-forward
  channel to the next brief as devclaw-controlled fact, bounded by the same
  budget, and MUST never be sourced from worker prose.
- **FR-011**: The done-gate MUST treat a clause with a current Decision as
  *resolved by decision*, cite the Decision as the clause's evidence, and
  MUST NOT re-litigate it; a later Decision on the same clause supersedes.
- **FR-012**: `steer_goal` MUST remain available for genuine direction
  changes and MUST stop being the documented answer to a Problem; the owner
  ping and console MUST point at the two verbs.
- **FR-013**: The zero-token idle guard, fail-closed gates, single-writer
  CAS, and the single ACHIEVE emitter MUST be untouched: no tick-path
  cognition is added, a Problem is raised only where a block is raised
  today, and a Decision never closes a goal except through the existing
  done-gate ACHIEVE path.
- **FR-014**: The persisted Problem/Decision shapes MUST ship their doctor
  check and seeded-fault tests (spec 016 FR-014), and the pings-per-goal-week
  metric MUST be computable mechanically from stored Problems and Decisions.

### Key Entities

- **Problem**: the typed statement a goal carries when it cannot proceed —
  what is wrong, clause, why undecidable by the loop, options, default,
  timebox, raised-by (done-gate / worker / churn park / admission), status
  (open / resolved / defaulted / superseded).
- **Option**: one bounded resolution a Problem offers — label, consequence,
  whether it is the default.
- **Decision**: the recorded answer — the Problem and clause it resolves, the
  chosen option or correction text, who made it, when, provenance (owner /
  defaulted / admission). Devclaw-controlled; fed forward; supersedable.
- **Contract clause**: one atomic `done_when` requirement, the unit a Problem
  and a Decision attach to; the done-gate already decomposes contracts into
  these.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Owner pings per goal-week fall by at least half against the
  2026-09-02 baseline (8 in one day across 10 goals), measured over the first
  two weeks after the whole spec lands.
- **SC-002**: The share of pings that genuinely needed judgement rises above
  half — the ratio flips from ~1 in 8 to a majority.
- **SC-003**: Every human resolution of a Problem in that window is a typed
  move; zero Problems are resolved by prose steering.
- **SC-004**: Replayed against the four 2026-09-02 authoring defects, the
  admission lint catches all four with a named reason and admits the
  corrected forms.
- **SC-005**: No done-gate round re-litigates a clause that carries a current
  Decision — zero verdicts cite an unsatisfied clause that has one.
- **SC-006**: The zero-token idle guard tests stay green throughout; an idle
  goal with an open Problem costs zero cognition calls.

## Assumptions

- The default timebox is 12 hours — long enough to span an overnight run,
  short enough that a goal never waits a full day on a defaulted answer.
- Options on a done-gate Problem come from the evaluator's own proposed
  resolutions; where it offers none, the fixed fallback set applies.
- The owner's chat surface (the waiter) is where a Problem is most often
  read and answered; it translates a chat reply into one of the two verbs.
  The verbs themselves are the contract; the chat is one client of them.
- A Problem's clause reference uses the done-gate's existing clause
  decomposition; a contract-level Problem (no single clause) is allowed.
- The admission lint is mechanical where it can be (baseline-less absolute
  predicates, known capability names) and uses the existing intake-readiness
  cognition only for the undecided-design-choice class, at creation time —
  never on the tick path.
- Existing goals with prose `blocked_on` and no Problem keep working; the
  console shows them as before. Nothing is migrated retroactively.
- Firming-phase `answer_unknowns` stays disabled; this spec does not revive
  it — the Problem/Decision shape is its successor.

## Rejected alternatives

- **Structured prose only** — keep `steer_goal`, just format the block
  better. Rejected: the answer stays unlinked from the question; nothing
  records answered-vs-redirected; the done-gate still re-asks.
- **More than two verbs** (accept, defer, split, override…). Rejected: the
  principle is two moves; everything else is an *option* inside *decide*.
- **Defaults that park instead of act**. Rejected: a park is the current
  failure with a countdown; the whole point is the loop keeps moving and the
  owner reviews decisions rather than gating them.
- **Feed Decisions through the steering inbox**. Rejected: the inbox is
  worker-facing prose the brief consumes and forgets; Decisions are facts
  with provenance and must survive across increments (#358 boundary).
- **Revive firming/`answer_unknowns`** as the Q&A surface. Rejected: it is
  pre-dispatch only, disabled in production, and has no notion of clause,
  option, default, or timebox.

## Clarifications (2026-09-02, walked with Denys one at a time)

- **Q1 — Prose steering while a Problem is open** → **A: refused.** `steer_goal`
  on a goal with an open Problem is refused; the response carries the Problem
  and the two verbs. A real direction change resolves the Problem first
  (*decide → split* / *cancel*), then steers. Rejected: B (accept, Problem
  stays open — the unlinked-strings failure in a new coat) and C (accept and
  auto-close as superseded — a prose steer silently resolving a typed
  question is exactly the shape ruled out). Encoded in US2 scenario 4, FR-006.
- **Q2 — May a defaulted Decision close a goal?** → **C: rides the strictness
  dial.** Under `trust` a defaulted *accept and close* closes through the
  existing done-gate ACHIEVE path (merge-on-close, gap recorded as a follow-up,
  one notice); under `strict` the timeout parks with the Problem open. Same
  class as advise-and-ship vs hold (constitution V) — no new knob. Rejected:
  A (never — a done-enough goal waits for a human, against the spec's goal)
  and B (always — a wrong default merges a known gap with no guard at all).
  Encoded in US2 scenario 5, FR-007.
- **Q3 — A clause the sandbox can never satisfy: refuse or rewrite?** → **A:
  refuse.** Creation fails naming the clause and the capability; nothing is
  persisted; the author rewrites and resubmits. The author stays the author.
  Rejected: B (rewrite and admit — the lint guesses what was meant and the
  guess becomes a settled fact the gate enforces, the one thing US4 makes
  hard to undo) and C (admit + hold with a defaulting Problem — B with a
  countdown). The other two lint classes keep their mechanical treatment
  because they are different in kind: "all tests pass" has an unambiguous
  rewrite; an undecided design choice *is* a choice and gets options.
  Encoded in US3 scenario 1, FR-009.
