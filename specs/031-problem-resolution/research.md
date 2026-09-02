# Research: Structured problem resolution (Phase 0)

Every design unknown in the plan's Technical Context, resolved against the
codebase as it stands on `main` at `4dfa0b9`. No external research was needed:
each decision is a choice between shapes the repo already has.

## R1. Where a Problem lives

- **Decision**: two append-only tables, `goal_problems` and `goal_decisions`,
  plus one pointer column `goal_status.problem_id` (the current open Problem,
  empty when none). A Problem is *alongside* `phase="blocked"`, never a new
  `State`.
- **Rationale**: `goal_status` is the CAS'd single-writer row; a pointer column
  keeps "is a Problem open?" a one-column read for the tick's blocked branch
  and for `steer_goal`'s refusal (zero-token, no join). The tables keep
  history (superseded Problems and Decisions are facts, not overwrites) — the
  same append-only shape as `goal_deliveries` / `goal_settlements`.
- **Alternatives considered**: (a) JSON blob in `goal_status.blocked_on` —
  rejected: prose and structure in one column, and history is lost on
  overwrite; (b) a new `State.PROBLEM` — rejected: `LEGAL` grows for no
  behaviour gain, and every existing blocked-path guard would need a sibling;
  (c) Problems in the steering inbox — rejected by the spec (unlinked
  strings).

## R2. How resolution unblocks

- **Decision**: `GoalService.resolve_problem(goal_id, problem_id, verb, …)`
  writes the Decision row and performs `Event.UNBLOCK` through
  `GoalStore.transition(expect=)` with the *same* reset shape as `steer_goal`
  (`actions_dispatched=0`, `heal_attempts=0`, `donegate_rounds=0`,
  `donegate_progress=0`, `merge_heal_attempted=False`, `problem_id=""`), all in
  one transaction. No new `Event`.
- **Rationale**: FR-004 says a human resolution restores budgets exactly as
  steer/resume do; reusing `UNBLOCK` keeps `LEGAL` untouched and the
  single-writer story unchanged. One transaction = the Decision and the
  unblock are atomic (a `TransitionConflict` rolls both back).
- **Alternatives**: a new `Event.RESOLVE` — rejected: identical target state,
  only a label; the structural guard on the transition table would need
  changing for nothing.

## R3. The raise seam — one function, four sites

- **Decision**: `goal/problems.py::raise_problem(store, goal_id, *, kind,
  what, clause, why, options, default, timebox, raised_by)` is the ONLY way a
  Problem is created. Callers: `tick_donegate.py` (evaluator `needs_human`;
  the churn park), `tick_settle.py` (worker honest-block detail), `tick.py`
  dispatch-time `needs_answer` (the slice re-slice park). Each call is inside
  the caller's existing `BLOCK` transaction; `blocked_on` keeps a one-line
  summary for pre-spec readers (FR-002).
- **Rationale**: constitution VII — four raise sites today, each writing its
  own prose; the class fix is one seam. Placing the write inside the existing
  transaction keeps atomicity and the single writer.
- **Alternatives**: raise Problems lazily on read (derive from `blocked_on`)
  — rejected: it re-parses prose, the thing the spec removes.

## R4. Worker honest-block: raise immediately, don't burn the cap

- **Decision**: in the settle path, a failed poll whose detail carries the
  worker-blocked marker raises a Problem (kind `needs_answer`, options
  *supply the capability / correct the requirement / cancel*, default
  *correct the requirement*) and blocks the goal on that settle — instead of
  counting toward the dispatch cap and parking two runs later as
  `mechanical:dispatch_cap`.
- **Rationale**: the block is already fail-fast at the task layer (not
  retried); letting the goal layer re-dispatch an identical block is the loop
  spending a session to rediscover a known fact. Today's `issue-414` took
  the long road.
- **Alternatives**: keep the cap path and attach a Problem at the cap park —
  rejected: two wasted dispatches per block, and the Problem would name the
  cap, not the cause.

## R5. Where options come from

- **Decision**: done-gate Problems take options from the evaluator's own
  `corrections` (one option per correction, first = default) plus the fixed
  tail *accept and close* / *split into a follow-up*; churn parks use the
  fixed set *correct the implementation / accept and close / split into a
  follow-up* (default *correct the implementation*); worker blocks use the
  set in R4. Two to five options, always (FR-001); when the evaluator offers
  none the fixed fallback applies and the Problem says so (loud, VI).
- **Rationale**: the evaluator already produces the actionable corrections;
  promoting them to options costs no cognition. Fixed sets for the other two
  raisers because neither has cognition in hand.
- **Alternatives**: a cognition call to synthesise options at raise time —
  rejected: tick-path cognition on a block (III).

## R6. Timebox and the defaulted close

- **Decision**: default timebox 12h, stored as `timebox_at` on the Problem.
  The tick's blocked branch (before `should_plan`) checks `now >= timebox_at`
  for a goal with a `problem_id` — one timestamp compare — and applies the
  default as a Decision with provenance `defaulted`, then `UNBLOCK`s and
  notifies once. If the default option is *accept and close*: under `trust`
  the Decision marks the clause resolved and the goal returns to idle, and
  the done-gate's next round grades it *resolved by decision* and closes
  through the existing ACHIEVE path; under `strict` no Decision is written,
  the goal stays blocked, and the owner is notified that only an explicit
  *decide* can close it (Q2 → C).
- **Rationale**: III (a timestamp compare, no cognition) and V (no second
  ACHIEVE emitter — the close is still the done-gate's verdict). The dial is
  the existing knob for "may the loop ship a known gap".
- **Alternatives**: close directly from the tick on a defaulted accept —
  rejected: a second ACHIEVE emitter breaks the structural guard and the
  merge-on-close ordering; park on any timeout — rejected by the spec
  (defaults act).

## R7. Feed-forward of Decisions

- **Decision**: `goal/decisions.py::render(rows)` mirrors
  `prior_increments.render`: a `DECISIONS_MARKER` head line, one compact line
  per *current* Decision (clause · choice · provenance · date), tail-kept
  under a `prompt_budget` cap; superseded Decisions are not rendered. Slotted
  into the advance brief directly after the prior-increments section. Only
  devclaw-controlled fields are rendered — never the worker's prose (#358).
- **Rationale**: US4 says "the same channel"; the existing section already
  has the marker/budget/trust-boundary machinery.
- **Alternatives**: inject Decisions as steering lines — rejected by the spec
  (the inbox is consumed and forgotten).

## R8. The done-gate reads Decisions

- **Decision**: `evaluator.evaluate(..., decisions: str | None = None)` — a
  blank-safe kwarg rendered as a `DECISIONS` block in the grounding context.
  The prompt gains one rule: a clause that carries a current Decision is
  graded `resolved_by_decision` with the Decision cited as evidence and
  counts as satisfied; the parser accepts the new clause verdict kind.
- **Rationale**: FR-011; the same grounding pattern as `REPOSITORY CONTEXT`
  (#227 shape); blank-safe keeps every existing call site byte-unaffected.
- **Alternatives**: filter decided clauses out of `done_when` before the
  gate — rejected: the gate must *see* the decision to cite it, and the
  contract text stays the author's.

## R9. Admission lint placement and cost

- **Decision**: `goal/admission_lint.py::lint(done_when, workspace) ->
  LintResult(refusals, rewrites, problems)`; called from `create_goal_async`
  after the existing referenced-contract readiness check and before anything
  persists. Class (a) capability-impossible → refusal (Q3 → A) matched
  mechanically against a small vocabulary (credential / token / API key /
  login / a human confirms / Telegram / Slack / email / external service
  names from the manifest's declared capabilities' complement); class (b)
  baseline-less absolute predicates → mechanical rewrite to "no new failures
  relative to the default branch", recorded as an admission Decision; class
  (c) undecided design choice → the existing intake-readiness cognition
  caller, at creation only, raising a Problem to the author before dispatch.
- **Rationale**: (a) and (b) are pattern-shaped and must be free and
  deterministic; (c) genuinely needs reading; creation is the one place a
  cognition call is already paid for (spec 019). Never on the tick.
- **Alternatives**: cognition for all three — rejected: makes a refusal
  non-deterministic and testable only by evals; a lint that only warns —
  rejected by Q3.

## R10. Read surface and notifier

- **Decision**: `get_goal` / `list_goals` / `goal_json` carry `problem`
  (the full object) and `decisions` (current ones); `blocked_on` keeps the
  one-line summary. HTTP gains `POST /goals/{id}/resolve` with body
  `{"verb": "correct_implementation"|"decide", "problem_id", "option"|"text"}`
  mirroring the MCP verbs. The owner ping names the clause, lists the options
  with the default marked, and names the two verbs — never `steer_goal`.
- **Rationale**: FR-002, FR-012; one service entry, three clients.

## R11. Doctor

- **Decision**: `instance.problems.tables` (both tables present) and
  `instance.problems.status_pointer` (`goal_status.problem_id` present; no
  row has a `problem_id` whose Problem is not `open`) with seeded-fault
  pairs — mirroring `check_goal_status_slice_hold_count` and
  `check_merge_on_close_columns`.
- **Rationale**: spec 016 FR-014; the second invariant catches the
  pointer/row drift the stubbed suite cannot see on a live DB.
