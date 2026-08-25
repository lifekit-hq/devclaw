# Feature Specification: Scorecard Measures the Ratchet

**Feature Branch**: `018-scorecard-ratchet`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "Fix the L8 scorecard so its headline metrics measure what the autonomy thresholds ratchet on: per-PR ground-truth merge rate, per-goal first-pass rate, a steering split (machine correction vs human steers), and the 'finished' threshold values surfaced as pass/fail — so the spec 007 autonomy flip is decided by numbers that are actually true."

## Clarifications

### Session 2026-08-25

- Q: Where does PR ground truth get read — live poll at compute time (A),
  persisted ledger refreshed outside the read path (B), or lazy-TTL hybrid
  (C)? → A: **B.** The scorecard stays a pure instant store read; a bounded
  refresh runs off-tick (nightly cycle-report run as owner); staleness is
  stamped in the output. Day-stale is honest enough because merges happen on
  human time. (Rejected: A puts network + credential dependence into a read
  surface; C adds moving parts without a need the stamp doesn't cover.)

## Why (context the requirements hang off)

The 2026-08-25 audit of `compute_scorecard` found that two of the three
headline metrics do not measure what their names claim:

- `merge_rate` (reported 0.50) counts done task rows carrying a `pr_url` —
  a PR *opened*, never merged. Its denominator includes done-gate review
  tasks that cannot have PRs by design (33 of 72 that week), and its
  numerator counts task rows, not PRs — goal-branch increments share one
  cumulative PR, so 36 rows collapsed to 18 distinct PRs. Ground truth read
  from GitHub by hand: 11 merged / 13 decided ≈ 0.85.
- `first_pass_hit_rate` (reported 0.36) is `achieved / all evaluator
  verdicts` — verdict-weighted, so one goal churning 6 done-gate rounds
  dominates a week. The per-goal datum (rounds to close) is destroyed at the
  moment it matters: the close transition zeroes the round counter. Ground
  truth reconstructed from the eval-outcomes ledger: 5 of 11 achieved goals
  closed on their first proposal ≈ 0.45, median 2 rounds.
- `steer_rate` (0.61) counts only the machine's own off_track corrections;
  the owner's real steers are stored (steering rows carry a source) but
  never read.

The corrected picture inverts the diagnosis: output quality is nearly at bar
(the human merges ~85% of decided PRs); convergence is what fails (~45%
first-pass). The scorecard is the gate for flipping spec 007 autonomy on —
ratcheting on numbers this wrong means flipping (or refusing to flip) on
noise. Fix the ruler before the ratchet.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Per-goal convergence: first-pass rate and rounds-to-close (Priority: P1)

As the operator, when I read the scorecard I see, for the window: how many
goals closed achieved, what fraction of them closed on their FIRST done
proposal, and the distribution (median / max) of done-gate rounds each goal
needed. This is the metric that is actually failing today and the primary
ratchet input.

**Why this priority**: The convergence number decides everything downstream
(goal-authoring discipline, the churn brake, the 007 flip). It is also the
only headline metric whose raw datum is currently destroyed at write time —
until the round count survives the close, no amount of read-side cleverness
can compute it.

**Independent Test**: Seed a store with three goals — one closing on its
first done proposal, one closing on its third, one cancelled before any
proposal — and verify the scorecard reports first_pass 1/2, median rounds 2,
and the cancelled goal excluded from the convergence denominator but counted
in an abandonment figure.

**Acceptance Scenarios**:

1. **Given** a goal that closes achieved on its first done proposal, **When**
   the scorecard is computed over a window containing the close, **Then** it
   counts toward the first-pass numerator and denominator, and its recorded
   rounds-to-close equals 1.
2. **Given** a goal that needed N (>1) done-gate rounds before closing,
   **When** the scorecard is computed, **Then** the goal counts in the
   denominator only, and N appears in the rounds distribution.
3. **Given** a goal cancelled before any done proposal, **When** the
   scorecard is computed, **Then** it appears in an abandoned/cancelled count
   and does NOT dilute the first-pass denominator.
4. **Given** a goal closed BEFORE this feature ships (no persisted round
   count), **When** the scorecard is computed over a window containing it,
   **Then** the goal is reported under a "rounds unknown" bucket — never
   silently counted as first-pass.

---

### User Story 2 - Ground-truth merge rate per PR (Priority: P2)

As the operator, when I read the scorecard I see: distinct PRs opened in the
window, how many are merged / rejected / still open (state read from the
hosting platform, not inferred), and the decided-PR merge rate
(merged / (merged + rejected)). PRs from bench/evidence projects are
reported separately so shakedown noise never moves the ratchet.

**Why this priority**: This is the trust metric for the 007 flip — "of what
it ships, how much do I actually land". Second priority only because the
hand-audit shows it is already near bar; the number needs to be *true*, not
*improved*.

**Independent Test**: Seed deliveries pointing at a mix of PR states
(merged, closed-unmerged, open, and one from a project marked bench) and
verify distinct-PR counting, the decided-rate arithmetic, the open PRs
excluded from the decided denominator, and the bench PR segregated.

**Acceptance Scenarios**:

1. **Given** three done task rows sharing one cumulative goal-branch PR,
   **When** the scorecard is computed, **Then** that PR is counted once.
2. **Given** a window whose distinct PRs are 2 merged, 1 closed-unmerged,
   1 open, **When** the scorecard is computed, **Then** it reports
   opened=4, merged=2, rejected=1, open=1, decided_merge_rate=2/3 — the open
   PR is in no rate's denominator.
3. **Given** a PR whose state cannot be determined (repo deleted, platform
   unreachable), **When** the scorecard is computed, **Then** the PR is
   reported under an explicit "unknown" count — never assumed merged or
   rejected, and the computation does not fail.
4. **Given** a project marked as bench/evidence, **When** its PRs fall in
   the window, **Then** they appear in a separate bench figure and are
   absent from the ratchet-facing rate.
5. **Given** the scorecard read surface is invoked, **When** no fresh
   platform state is reachable, **Then** the metric degrades loudly (staleness
   or unknowns named in the output) rather than silently reporting the last
   known numbers as current.

---

### User Story 3 - Steering split: machine correction vs human steers (Priority: P3)

As the operator, I see two separate numbers where today's `steer_rate`
conflates them: (a) machine self-correction, expressed per goal as the
rounds-to-close distribution from Story 1 — not per verdict; (b) human
steering — the count of owner-written steering lines in the window, read
from the steering store where they already live, distinguished from
machine-appended (`auto-*` source) lines.

**Why this priority**: Least urgent — Story 1's rounds distribution already
replaces the machine half; this story adds the human half and retires the
misleading single number.

**Independent Test**: Seed steering rows with mixed sources (owner and
`auto-eval`) across two goals and verify the human-steer count includes only
the owner rows, and the legacy `steer_rate` field is either removed or
explicitly labeled as the machine-only proxy.

**Acceptance Scenarios**:

1. **Given** a window containing 3 owner steering lines and 5 machine
   (`auto-*`) correction lines, **When** the scorecard is computed, **Then**
   human_steers=3 and machine lines are not counted as human steers.
2. **Given** the new fields exist, **When** a reader consumes the scorecard,
   **Then** no field named to imply human steering is computed solely from
   machine verdicts.

---

### User Story 4 - The finish line, machine-checked (Priority: P2)

As the operator, the scorecard tells me pass/fail against the agreed
autonomy thresholds — the spec 007 flip gate — so "are we finished?" is a
number I read, not a feeling. Initial threshold values (agreed 2026-08-25,
tunable as configuration without a code change):

- per-goal first-pass rate ≥ 0.70
- decided-PR merge rate ≥ 0.80 (bench projects excluded)
- sustained over a rolling two-week window with zero mechanism-wedge nights

**Why this priority**: This is the point of the whole feature — the numbers
exist to gate a decision. It rides on Stories 1–2 being true first.

**Independent Test**: Seed a store whose corrected metrics sit just above
and just below each threshold and verify the pass/fail verdict per metric
and the overall gate verdict flip accordingly.

**Acceptance Scenarios**:

1. **Given** corrected metrics above every threshold across the rolling
   window, **When** the scorecard is computed, **Then** each metric reports
   pass and the overall autonomy-gate line reports pass.
2. **Given** any one metric below its threshold, **When** the scorecard is
   computed, **Then** that metric reports fail, the overall gate reports
   fail, and the failing metric is identifiable.
3. **Given** threshold values changed via configuration, **When** the
   scorecard is next computed, **Then** the new values apply and are echoed
   in the output.
4. **Given** the gate reports pass, **Then** nothing auto-flips — spec 007's
   ruling stands: the scorecard informs the human flip, it never performs it.

---

### Edge Cases

- A goal cancelled *between* done proposals (some rounds recorded, never
  closed): counts as abandoned, its rounds excluded from the closed-goal
  distribution.
- A PR opened in-window but decided after the window's end: state is read as
  of computation time — it counts by its current state (the scorecard is a
  rolling view, not a frozen ledger).
- Two goals delivering to the same PR (should be impossible under
  goal-branch; if observed): counted once, flagged in the output.
- Re-opened PR (closed, then reopened): current state wins — open, in no
  decided denominator.
- Window containing zero closed goals or zero decided PRs: rates report as
  absent/None with the zero denominators named — never as 0% or 100%.
- Pre-feature historical windows: convergence metrics report "unknown"
  buckets (US1 scenario 4) rather than mixing regimes silently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The number of done-gate rounds a goal consumed MUST survive
  the goal's close as durable, queryable state; the close transition MUST
  NOT destroy it. (Today it is zeroed at close.)
- **FR-002**: The scorecard MUST report per-goal convergence over the
  window: goals closed achieved, first-pass fraction (rounds == 1), median
  and max rounds-to-close, plus counts of abandoned goals and of closed
  goals whose rounds are unknown (pre-feature closes).
- **FR-003**: The scorecard MUST count PRs, not task rows: distinct PRs
  opened in-window, each counted once regardless of how many increments
  delivered into it.
- **FR-004**: PR merge state MUST come from the hosting platform's ground
  truth (merged / closed-unmerged / open), never inferred from the presence
  of a `pr_url`; an undeterminable state lands in an explicit "unknown"
  bucket and never fails the computation (fails loud, not closed — this is
  telemetry, not a gate).
- **FR-005**: The ratchet-facing merge metric is the decided-PR merge rate:
  merged / (merged + rejected). Open and unknown PRs appear in counts but in
  no rate denominator.
- **FR-006**: A project MUST be markable as bench/evidence; its PRs and
  goals are reported separately and excluded from every ratchet-facing rate.
- **FR-007**: The scorecard MUST report human steering (owner-written
  steering lines in-window, identified by their stored source) separately
  from machine correction; no reported field may imply human steering while
  being computed from machine verdicts. The legacy conflated `steer_rate`
  is removed or explicitly relabeled as machine-only.
- **FR-008**: Autonomy threshold values MUST live as configuration (with the
  agreed 2026-08-25 defaults), be echoed in the scorecard output, and each
  ratchet metric MUST carry a pass/fail against its threshold plus one
  overall autonomy-gate verdict. The gate verdict is informational only —
  no mechanism flips spec 007 autonomy from it.
- **FR-009**: Every metric definition MUST be pinned by a named regression
  test over a seeded store (and a stubbed platform-state source); the
  hand-audited 2026-08-18..25 shapes (row-vs-PR collapse, review-task
  denominator pollution, churny-goal verdict weighting) become seeded test
  cases.
- **FR-010**: The scorecard read surface stays a pure, zero-cognition,
  zero-network store read. PR ground truth is held in a persisted delivery
  ledger refreshed OUTSIDE the read path (decided 2026-08-25, option B): a
  bounded refresh — at most one state lookup per distinct undecided
  in-window PR — owned by an existing off-tick mechanism step (the nightly
  cycle-report run is the natural owner), never by the idle heartbeat path.
  Every scorecard output stamps the ledger's as-of time; a stale or
  never-refreshed ledger is reported as such (US2 scenario 5), never
  presented as current.
- **FR-011**: Existing consumers of the scorecard surface (CLI, MCP tool,
  any dashboard feed) receive the corrected metrics without breaking:
  renamed-away fields are removed deliberately, not silently re-defined —
  a field keeping its old name MUST keep its old meaning.
- **FR-012**: Usage/token figures remain reported but MUST be labeled as
  non-ratchet telemetry; no threshold attaches to them while cognition input
  tokens exclude cache reads (the 492-in / 236k-out incoherence stays
  visibly estimate-grade until the envelope improves).

### Key Entities

- **Goal convergence record**: per closed goal — rounds consumed at close,
  outcome (achieved / abandoned), close time. The datum FR-001 preserves.
- **Delivered PR**: a distinct PR devclaw opened — identity (URL), owning
  project, opened time, last-known platform state (+ as-of time when state
  is persisted per FR-010's resolution).
- **Steering line**: already stored with a source; this feature only reads
  the human/machine distinction it already carries.
- **Autonomy thresholds**: named metric → threshold value + window length;
  configuration, not code.
- **Bench project marker**: a per-project flag separating evidence/shakedown
  work from ratchet-facing work.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any seeded week, the scorecard's merged/rejected/open PR
  counts exactly match a by-hand platform audit of the same PRs (the
  2026-08-18..25 audit week is the reference fixture: 18 distinct PRs, not
  36 rows).
- **SC-002**: For any seeded week, first-pass and rounds figures exactly
  match a by-hand count over the eval ledger (reference: 5/11 first-pass,
  median 2).
- **SC-003**: A goal churning N rounds shifts the first-pass rate by exactly
  one goal's weight — never by N verdicts' weight.
- **SC-004**: The operator can answer "does the autonomy gate pass, and if
  not which metric fails?" from a single read of the scorecard output, with
  no manual arithmetic.
- **SC-005**: Zero cognition calls and zero platform calls occur on idle
  heartbeat ticks with this feature in place (existing zero-token guard
  tests stay green, extended to the new persistence write).
- **SC-006**: Bench-project activity moves no ratchet-facing number: adding
  a bench PR/goal to a seeded window changes only the bench figures.

## Assumptions

- The eventual-consistency of PR state is acceptable: the scorecard is a
  rolling operator view; a PR decided after computation shows its new state
  on the next read. No frozen historical ledger is required.
- Bench/evidence marking lives on the project registration (a per-project
  flag set by the operator), not on name patterns; the three shakedown/bench
  repos observed in the audit week would carry it.
- Goals cancelled before any done proposal are "abandoned" and excluded from
  convergence denominators; this matches the operator's intuition that a
  goal he killed says nothing about the loop's convergence.
- Threshold defaults are the 2026-08-25 agreement (first-pass ≥ 0.70,
  decided-merge ≥ 0.80, two-week wedge-free window); they are starting
  values expected to be tuned as configuration, and the wedge-free condition
  is read from the existing nightly cycle reports.
- The platform is GitHub today; the ledger refresh goes through the same
  authenticated seam existing delivery/remote-check code already uses — no
  new credential class. (Which seam exactly is a plan-level choice; the
  where-it-runs question is settled by FR-010: off-tick, ledger-refresh.)
- Spec 007's governance stands unchanged: the flip is manual; this feature
  only makes the informing numbers true.

## Rejected Alternatives (direction memory)

- **Keep verdict-weighted rates and add caveats**: rejected — the audit
  showed the weighting inverts the diagnosis (0.36 vs 0.45 measuring
  different things by coincidence-close numbers); a caveated wrong number
  still ratchets wrong.
- **Infer merge state from branch/commit reachability locally**: rejected —
  re-derives "what landed" instead of reading ground truth; the repo's own
  doctrine is one mechanical answer per question, and the platform owns
  merge state.
- **Auto-flip autonomy when the gate passes**: rejected — spec 007's
  clarification Q&A already ruled the flip is a manual operator act; the
  scorecard informs, never actuates.
- **Fix by hand-auditing weekly instead of fixing the metrics**: rejected —
  the audit took a session of expert time; the ratchet needs a number the
  operator can read daily.
