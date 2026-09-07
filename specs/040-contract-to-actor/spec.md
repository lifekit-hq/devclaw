# Feature Specification: The contract reaches the actor

**Feature Branch**: `040-contract-to-actor`

**Created**: 2026-09-07

**Status**: Draft — clarified 2026-09-07 with Denys (5 questions, all encoded below); ready for `/speckit-plan`; nothing is implemented

**Input**: User description: "The completion contract reaches the actor: the worker gets the same numbered clause list the done-gate judges, and a red CI verdict carries its failing log" (issue #780, plus the CI-log sibling surfaced by the finance-sentry environment holds of 2026-09-06/07)

## Why this exists

The loop judges at a resolution it never communicates, twice.

**The rubric.** The done-gate decomposes `done_when` into atomic clauses,
pins them once per contract revision (spec 035, `devclaw/goal/clause_pin.py`,
ids `c1..cN` minted mechanically), and demands specific repo evidence per
clause. The worker never sees that list. `_advance_brief`
(`devclaw/goal/tick.py`) hands it the contract as prose — the saga framing
for an explicit `done_when`, the referenced issue's live text for a pointer
goal — and the worker's own self-review skill
(`runner/skills/_writes-code/45-self-review.md`) tells it to review "against
the contract: which clause is unsatisfied", so the worker decomposes the
prose a SECOND time, differently. The 14-day scorecard read on 2026-09-07
is the bill: first-pass 0.24 over 25 closed goals, rounds median 2, max 12,
and the gate's refusal notes are one shape — "16 of 17", "17 of 18",
"eight of nine", "nine of ten" — the primary intent right, one secondary
clause missed, discovered a full round later.

**The verdict.** Since spec 032 the project's CI is the verdict of record.
When the rollup is red, `_ci_correction` (`devclaw/goal/tick_guards.py`)
steers back the failing check NAMES and tells the worker to "read the
failing check's log" — a log the sandbox cannot read: it carries no GitHub
credential by design, and `/actions/jobs/<id>/logs` answers 403. On
2026-09-06/07 a finance-sentry worker said so
(`BLOCKED: env — no GitHub token with actions:read`), which parked FOUR
goals on a project-wide `mechanical:env` hold for a capability the sandbox
must never have. The host reads the rollup through `gh` already
(`devclaw/goal/remote_checks.py`); it has the log the worker needs.

Both are the same class: **the verdict of record is not delivered to the
actor.** Constitution IX orders the fix — a missing FACT first, supplied
through the protocol; an instruction second; a brake last, and only in the
protocol domain (what goes into the worker, what comes out).

## Clarifications

### Session 2026-09-07

- Q: Should the loop pay one evaluator call per contract revision to decompose the clauses BEFORE the first worker session, so round one already sees the rubric? → A: Yes — pay the call at dispatch; one decomposition per revision, on the dispatch path only, never on an idle or blocked tick. (Lazy gate-side pinning would leave round one, where first-pass is lost, blind; creation-time pinning was rejected because a pointer goal's contract is live.)
- Q: When the worker's hand-back reports a clause as UNMET, should the loop skip the done proposal and dispatch the next increment directly, instead of proposing done and letting the gate refuse? → A: Skip the proposal and dispatch the next increment, in both strictness modes. The worker's admission is a fact about its work, never evidence about the repository; the gate runs when the worker claims every clause and still judges every clause before any close.
- Q: How much of a failing CI job's log should the red-verdict correction carry to the worker? → A: A bounded tail in the brief: per failing check, the last 120 lines of the failed step, ANSI stripped, redacted, inside the existing steering cap; the bound is one config value. (A file in the checkout needs a write outside the materialize span; a per-ecosystem error filter is project-tooling knowledge devclaw must not hold, constitution IX.)
- Q: In the dispatch brief, should the numbered clause list sit NEXT TO the contract prose the worker gets today, or REPLACE it? → A: Next to the prose, bounded: the prose carries the intent, the list carries the rubric; the list is capped the way steering is and the plan measures the brief budget (spec 021) against it. A pointer goal's issue text stays whole.
- Q: Should the worker's per-clause report be REQUIRED by the settle from the start, or advisory first? → A: Advisory first: an omitted pinned id is logged as a protocol finding and the gate judges as today; the ratchet to required (a malformed hand-back fails closed) waits for the compliance eval showing the worker follows the format. Not tied to the strictness dial.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The worker reads the rubric it will be judged by (Priority: P1)

Before the first worker session of a contract revision, the loop decomposes
the contract ONCE — the same decomposition the done-gate pins — and every
dispatch brief for that revision carries the pinned clauses as a numbered
list (`c1..cN`, verbatim text), directly after the contract prose. The
done-gate judges exactly that pin. A change to how clauses are derived
moves both at once; they cannot drift.

**Why this priority**: It attacks the failing scorecard metric directly and
at its root — the worker discovers "16 of 17" while it still has a session,
not after a gate round.

**Independent Test**: Seed a goal with a two-clause `done_when` and a
`FakeClaude` whose decomposition returns two clauses. Dispatch once: the
brief contains `c1` and `c2` with the pinned text; the pin row exists for
the revision; the evaluator call count is exactly one. Dispatch a second
increment on the same revision: the brief carries the same ids, call count
still one. Tick an idle goal: call count zero (the existing zero-token
tests, unmodified).

**Acceptance Scenarios**:

1. **Given** a goal with an explicit `done_when` and no pin for its
   revision, **When** the first increment is dispatched, **Then** the
   brief carries a `Contract clauses` block listing every pinned clause by
   id, and a `goal_contract_pins` row for the revision exists before the
   session starts.
2. **Given** a pointer goal whose contract is read live from its issues,
   **When** dispatched, **Then** the clauses are decomposed from the same
   live text the gate will read, with the ceremony drops of evaluator step
   1a applied (a clause about merging, PR count, branch or issue-closing
   never reaches the worker), and the revision digest logged is the one
   `_live_contract` computes.
3. **Given** a pin already exists for the revision, **When** any later
   increment is dispatched, **Then** the brief carries the pinned list
   unchanged and no cognition call is made for it.
4. **Given** the decomposition returns no clauses or the call fails,
   **When** dispatching, **Then** the goal blocks legibly
   (`needs_answer` with a typed Problem, spec 031) and no worker session
   starts — an empty list never reads as "nothing to satisfy".
5. **Given** the contract revision changes (a pointer goal's issue is
   edited), **When** the next increment is dispatched, **Then** the
   re-pin follows spec 035 US3 (carry-forward of byte-identical clauses,
   exactly one re-pin) and the brief carries the new list.

---

### User Story 2 — A red verdict carries its evidence (Priority: P2)

When the delivered PR's CI rollup is red, the correction steered back to the
worker carries, for each failing check, the bounded tail of the failed
job's log as a fact — read by the host through the same `gh` the rollup
already comes from. The worker never needs a GitHub credential to act on
the verdict, and a worker report that it "cannot read the CI log" is no
longer a possible environment deficiency.

**Why this priority**: The 2026-09-07 holds parked four goals on a
capability the sandbox must never have. Delivering the fact removes the
whole class of that hold; it is a one-seam change on a path that already
runs `gh`.

**Independent Test**: A fake `RemoteChecker` returns `failing` with one
failing job; a fake log reader returns 300 lines. The steering line
appended by `_autoheal_ci` contains the check name, the head sha and the
last N lines (N = the configured bound), ANSI stripped, and nothing else
from the log. A log reader that raises produces the correction exactly as
today plus one line stating the log could not be read — never a wedge.

**Acceptance Scenarios**:

1. **Given** a red rollup with two failing checks, **When** the CI heal
   steers the fix, **Then** the correction carries one bounded excerpt per
   failing check, each labelled with the check name and job id, in the
   order the rollup listed them.
2. **Given** the log read fails or times out, **When** steering, **Then**
   the correction is the current text plus "log unavailable: <reason>",
   the goal is not blocked, and the heal budget is charged exactly as
   today.
3. **Given** the excerpt, **When** the worker's next session starts,
   **Then** the brief's failure/steering section carries it verbatim
   within the existing steering cap, and the worker skill for red CI says
   "the failing log is in your brief; do not try to fetch it".
4. **Given** a worker that still reports `BLOCKED: env — no GitHub token
   ... logs`, **When** the settle classifies it, **Then** the admission
   lint names it as a capability the sandbox must not have (spec 031 lint
   class, already refused at creation) and it never becomes a
   `mechanical:env` hold.

---

### User Story 3 — The worker answers the rubric, clause by clause (Priority: P3)

The worker's completion report addresses every pinned clause by id — with
the evidence it claims, or `UNMET: c<n> — <why>`. The settle parses the
report; an `UNMET` clause is a fact about the worker's own work: the thin
path does not propose done on it and dispatches the next increment with
the unmet clause named, instead of spending a gate round to be told the
same thing. The done-gate still judges every pinned clause against the
repository before any close (the report is a claim, never evidence).

**Why this priority**: Converts the gate's "one secondary clause missed"
refusals into the worker's own next increment. Depends on US1 (the ids)
and is the protocol half of the change; useful without US2.

**Independent Test**: Seed a pinned two-clause goal; the fake worker's
summary reports `c1` with evidence and `UNMET: c2 — needs the fixture`.
After settle: no done proposal, the next dispatch brief names `c2` as the
unmet clause, and the evaluator was not called. A summary that reports
every clause satisfied proposes done exactly as today.

**Acceptance Scenarios**:

1. **Given** a hand-back that lists every pinned id as satisfied, **When**
   settled, **Then** the done proposal proceeds exactly as today.
2. **Given** a hand-back with one `UNMET` id, **When** settled, **Then**
   the goal does not propose done, the log names the clause, and the next
   increment's brief carries it under the steering marker.
3. **Given** a hand-back that omits pinned ids, **When** settled, **Then**
   the omission is logged as a protocol finding (advisory in this spec,
   clarified 2026-09-07) and the done proposal proceeds; the gate judges
   as today.
4. **Given** the goal is under `strict`, **When** an `UNMET` clause is
   reported three increments in a row with the same id, **Then** the
   existing churn brake parks the goal (`donegate_churn`) with a typed
   Problem naming that clause.

---

### Edge Cases

- A goal created before this spec (no pin, revision unknown): the first
  dispatch after deploy decomposes and pins exactly as a new goal; existing
  pins from the gate are reused as-is (same table, same ids).
- A contract that decomposes to ONE clause: still rendered as `c1` — the
  worker's report protocol is uniform.
- An `UNMET` id that is not in the pin: logged as malformed; treated as
  no report for that line (advisory); never blocks.
- A red rollup whose failing check has no job log (a required context
  that never ran): the excerpt says so; the correction is the current
  text.
- A log that contains a secret-looking token: the excerpt is passed
  through the existing redaction used for traces before it enters the
  brief.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The contract MUST be decomposed and pinned for a revision
  BEFORE the first worker session of that revision, on the dispatch path
  only, by the same code path the done-gate uses
  (`clause_pin.assign_ids` over the evaluator's decomposition, ceremony
  drops of step 1a applied). One decomposition per revision, reused by
  dispatch and gate alike.
- **FR-002**: Every dispatch brief for a pinned revision MUST carry the
  pinned clauses as a numbered list (id + verbatim text) NEXT TO the
  contract prose it carries today — never instead of it — rendered by ONE
  generator shared with the gate's `Pinned clauses` block, so a rendering
  change moves both. The list is capped the way steering is (the cap
  truncates clause text, never drops an id); the plan measures the brief
  budget of spec 021 against a pinned list of the largest live contract.
- **FR-003**: A failed or empty decomposition MUST block the goal with a
  typed Problem (`needs_answer`, raised through `devclaw/goal/problems.py`)
  and MUST NOT dispatch — the empty-contract rule of spec 019 extends to
  the empty clause list.
- **FR-004**: The zero-token idle guard is untouched: an idle or blocked
  tick makes zero cognition calls; the decomposition call happens only
  when a dispatch is about to start. The existing `FakeClaude.calls == 0`
  tests stay green and unmodified.
- **FR-005**: A red CI rollup's correction MUST carry, per failing check,
  a bounded excerpt of the failed job's log, read by the host through
  `gh` with the same wall-clock bound as the rollup read; the bound is a
  config value (`config.py`, one home) with a default of 120 lines.
- **FR-006**: A log read that fails MUST degrade to the current
  correction text plus one line naming the failure; it never blocks, never
  raises into the tick, and charges the heal budget exactly as today.
- **FR-007**: The excerpt MUST pass through the existing secret redaction
  before it enters steering, and MUST fit the existing steering cap
  (`_cap_steering`) — the cap truncates the excerpt, never the
  instruction.
- **FR-008**: The worker hand-back protocol gains a per-clause report:
  one line per pinned id, `c<n>: <evidence>` or `UNMET: c<n> — <why>`,
  specified in `runner/skills/_writes-code/` (one home) and parsed at
  the settle (`devclaw/queue/settle.py`) into structured fields on the
  task result.
- **FR-009**: An `UNMET` report MUST suppress the done proposal for that
  settle, in both strictness modes, and the loop MUST dispatch the next
  increment with the unmet clause carried in its steering — no review and
  no evaluator call is spent on a gap the worker named. The done-gate's
  judgment is never replaced by the report (the report is a claim; the
  gate reads the repository) and runs when the worker claims every clause.
- **FR-010**: A hand-back that omits pinned ids is advisory in this spec:
  the omission is logged as a protocol finding and the gate judges as
  today, in both strictness modes. The ratchet to required (a malformed
  hand-back fails closed) is a later spec, gated on a compliance eval over
  live hand-backs — never on diligence.
- **FR-011**: The worker skill text MUST say, for a red verdict, that the
  failing log is in the brief and that fetching it is not a step; the
  `BLOCKED: env` skill line MUST exclude CI-log access from what a worker
  may report as missing.
- **FR-012**: No new table. `goal_contract_pins` is reused; the report's
  parsed fields ride the task result JSON the settle already stores.
- **FR-013**: The change ships its doctor check only if a persisted shape
  changes (spec 016 FR-014) — under FR-012 none does; the plan states so.

### Key Entities

- **ContractPin** (existing, spec 035): the per-(goal, revision) clause
  list; now written at dispatch, read by both brief and gate.
- **Clause report**: the worker's per-id claim parsed from the hand-back —
  `{id, satisfied: bool, note}` — stored on the task result; never a
  gate input.
- **CI excerpt**: `{check_name, job_id, head_sha, lines}` — a fact in the
  steering line, produced by the host, bounded and redacted.

## Success Criteria *(mandatory)*

- **SC-001**: First-pass rate over the 14-day scorecard window rises from
  0.24 (2026-09-07) toward the 0.70 ratchet; the read is taken 14 days
  after deploy, on the same `get_scorecard_metrics` definition.
- **SC-002**: Evaluator calls per achieved goal fall (3.3 on 2026-09-07):
  a clause the worker reports `UNMET` costs a worker session, not a gate
  round.
- **SC-003**: Zero `mechanical:env` holds whose capability row names CI
  log access, over the same window (the problems catalog, fingerprint
  `worker:no-github-token-with-actions-read*`).
- **SC-004**: The gate's refusal notes stop carrying the "N of N+1"
  shape for clauses the worker had in its brief — measured by hand over
  the window's refusals; if the shape persists, the worker is ignoring
  the list and the fix is an instruction (constitution IX), not a brake.

## Out of Scope — rejected, with reasons

- **The worker decomposes and the gate pins the worker's list.** Rejected:
  the party being constrained must not mint the keys (spec 035 doctrine).
- **The sandbox gets a read-only GitHub token for logs.** Rejected: the
  sandbox carries no credential by design (constitution I/II fence); the
  host already has the fact and the protocol is the right carrier.
- **Making the per-clause report a hard settle gate in this spec.**
  Deferred (clarified 2026-09-07): a protocol gate that fails green work is
  the fail-closed doctrine turned against the loop; ratchet it once an eval
  shows compliance.
- **Decomposing at goal creation.** Rejected: pointer goals' contracts are
  live; the revision at creation is not the revision the first dispatch
  works under.
- **Carrying the whole CI log.** Rejected: unbounded input into the least
  reliable call class; the bound is the fact the worker needs.

## Assumptions

- The evaluator's decomposition step can run in a decompose-only mode
  (no repository grounding required) at dispatch time; if it cannot, the
  plan adds that mode to `devclaw/goal/evaluator.py` as the ONE caller
  change, and the prompt file gains no new section (cognition-prompts
  rule: state each rule once).
- `gh run view --log-failed` (or the jobs API) is available on the host
  with the credential the rollup read already uses.
- The steering cap leaves room for a 120-line excerpt AND the pinned
  clause list; the plan measures the brief budget (spec 021) against both
  on the largest live contract and lowers the defaults if not.

## Post-landing corrections

_None yet._
