# Feature Specification: Loop Health Metrics

**Feature Branch**: `feat/loop-health-metrics`

**Created**: 2026-09-06

**Status**: Draft

**Input**: Owner session 2026-09-06 — "why the loop isn't running, and what a shipped increment costs"

## Why this exists

The owner's north star for devclaw, stated verbatim this session:

> devclaw runs 24/7 non-idle on planned work, nights come out clean, and it
> self-heals through stops (waking, resolving merge conflicts) instead of
> waiting for the owner.

A future issue-producing module (owner as gate) will feed it work.

That north star can fail in exactly three ways, and today only one of them is
measurable:

| Failure | Question | Measurable today? |
|---|---|---|
| The loop **stopped** when it shouldn't have | why is it not running? | ❌ `cycle_reports.idle` is a boolean with no cause |
| It **ran and produced garbage** | is the work any good? | ✅ first-pass rate (0.36), clean-cycle rate |
| It **ran but needed the owner** | can it run unattended? | ❌ self-heal counters exist but are never aggregated |

Separately, **cost history silently expires after 30 days**: token usage is read
out of `tasks.result_json` (`devclaw/telemetry.py:245`), and that column is
NULLed by retention (`TASK_RESULT_RETENTION_DAYS_DEFAULT`). No trend line over
the numbers is structurally possible, and the degradation is silent — the
figures simply get smaller.

This feature closes those gaps. It deliberately does **not** rebuild the
measurement surfaces that already work.

## Clarifications

### Session 2026-09-06

- Q: When devclaw is idle, which causes count against devclaw's own health score, and which are set aside as "not devclaw's failure"? → A: A three-way split — *devclaw-caused* / *owner's turn* / *no work available*. Health counts only devclaw-caused.
- Q: Should idle time be measured by sampling at each heartbeat tick, or by recording the exact moment the cause changes? → A: Tick-sampled — one write point on the heartbeat, resolution of one tick; sub-tick cause flapping is accepted as invisible.
- Q: Under goal-branch delivery one merged PR can represent a whole goal's worth of increments — what should "cost per merged PR" be divided by? → A: Both, segmented by delivery shape: cost per merged goal AND cost per standalone merged PR, reported separately, never blended into one average.
- Q: Should the console show a single headline number for loop health, and what should it measure? → A: Yes — a **not-stuck rate**: fraction of the window spent either working or legitimately idle (owner's turn / no work available). Only devclaw-caused idle pulls it down. Distinct from the rejected utilization metric, which punished legitimate idleness.
- Q: When the permanent usage record ships, should it backfill from un-pruned transcripts or start fresh? → A: Backfill what survives (~30 days available immediately); anything already pruned reads unknown, never zero.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Know why the loop is not running (Priority: P1)

The owner opens the console after a night and sees that the loop was idle for
six hours. Today that is all he can learn. He needs to know *which* of five very
different situations it was — an empty backlog, no goal armed, everything
planned already shipped, a mechanical block, or a quota pause — because each has
a completely different fix and only some of them are devclaw's fault.

**Why this priority**: It is the direct measure of the stated north star, and it
is the only one of the five metrics that is currently unmeasurable in any form.
It also gets materially more expensive to retrofit once the issue-producing
module exists, because that module makes owner-wait the dominant idle source.

**Independent Test**: Arm no goals, let a run window pass, then read the health
endpoint and confirm the idle period is attributed to `empty_backlog` rather
than counted as an undifferentiated idle boolean. Repeat with a goal blocked on
`needs_answer` and confirm the attribution changes accordingly.

**Acceptance Scenarios**:

1. **Given** the instance has no armed goals and the run window is open,
   **When** the heartbeat ticks and a caller reads the loop-health surface over
   HTTP, **Then** the elapsed idle time is attributed to `empty_backlog` and no
   other cause.
2. **Given** a goal is blocked with `blocked_kind = mechanical:merge_failed`,
   **When** the heartbeat ticks and a caller reads the loop-health surface,
   **Then** the idle time is attributed under that existing `blocked_kind`
   value verbatim, not under a newly invented name.
3. **Given** a goal is blocked with `blocked_kind = needs_answer` awaiting an
   owner decision, **When** a caller reads the loop-health surface, **Then**
   the idle time is attributed to that cause, is bucketed as *owner's turn*, and
   does not reduce the not-stuck rate.
4. **Given** the account is under a quota pause, **When** a caller reads the
   loop-health surface, **Then** the idle time is attributed to `paused` and no
   cognition call was made to determine that (zero-token idle guard holds).
5. **Given** a window containing working time, an owner-blocked period and a
   mechanically-wedged period, **When** a caller reads the loop-health surface,
   **Then** the not-stuck rate reflects only the wedged period as lost, the
   three-bucket breakdown is returned alongside it, and the buckets sum to the
   window.

---

### User Story 2 — Know whether devclaw fixes itself (Priority: P2)

The owner wants one number answering "can this run unattended?": of the problems
devclaw hit, what fraction did it recover from on its own versus what fraction
ended terminal or needed him.

**Why this priority**: It is the second north-star number ("self-heals through
stops") and the cheapest item in this spec — both counters already exist, are
already written on every problem record, and are already displayed per-problem.
Nothing aggregates them.

**Independent Test**: Seed a problems catalog containing a mix of recovered and
terminal records, read the health surface over HTTP, and confirm the reported
rate matches `recovered / (recovered + terminal)` with the two raw counts also
present.

**Acceptance Scenarios**:

1. **Given** the problems catalog holds records with both recovered and terminal
   counts, **When** a caller reads the loop-health surface, **Then** a self-heal
   rate is returned along with the raw numerator and denominator.
2. **Given** the problems catalog is empty, **When** a caller reads the surface,
   **Then** the rate is reported as unknown rather than as zero or as 100%.
3. **Given** a mechanical block self-heals during a run window, **When** the
   surface is read afterwards, **Then** the self-heal rate reflects that
   recovery without any manual step.

---

### User Story 3 — Keep cost history past 30 days (Priority: P3)

The owner wants to see token and cost trends over months, not a rolling window
that quietly truncates. Today the numbers live only inside a transcript field
that retention deletes.

**Why this priority**: It unblocks every trend view and every longitudinal
comparison in this spec, but the *current* month's numbers are already visible,
so it is less urgent than the two metrics that cannot be seen at all.

**Independent Test**: Settle a task, advance the clock past the transcript
retention horizon, run the pruner, and confirm the per-task usage figures are
still readable while the transcript itself is gone.

**Acceptance Scenarios**:

1. **Given** a task settles and its run reported usage, **When** the task's
   transcript is later pruned by retention, **Then** the task's token counts and
   cost figures remain readable.
2. **Given** a task settles and its run reported **no** usage, **When** the usage
   record is read back, **Then** it reads as *unknown*, never as zero.
3. **Given** a task was retried N times, **When** its usage is read, **Then** the
   task's cost is the sum across all attempts, and the attempt count is visible.
4. **Given** usage records exist across many months, **When** the storage
   footprint is measured, **Then** it remains a small fraction of total database
   size (see SC-006).

---

### User Story 4 — Know what a shipped increment costs (Priority: P4)

The owner is on a constrained quota. He needs to know what a *merged* increment
costs versus what is burned on runs that ship nothing — currently unanswerable,
because ground-truth merge state and token usage are never joined.

**Why this priority**: It is the number that converts "low quota" from a worry
into a decision input. It depends on US3 for any window longer than a month, so
it follows it.

**Independent Test**: With a set of settled tasks whose PRs carry known
ground-truth states, read the health surface and confirm cost is partitioned
into shipped-and-merged versus shipped-nothing, with the unknown-state remainder
reported separately rather than folded into either bucket.

**Acceptance Scenarios**:

1. **Given** settled tasks whose PRs are recorded as merged, spanning both
   goal-cumulative and standalone deliveries, **When** a caller reads the
   cost-per-outcome surface, **Then** cost per merged goal and cost per merged
   standalone PR are reported as two distinct figures, each with the count it is
   based on, and no blended average of the two is offered.
2. **Given** settled tasks that produced no shipped change, **When** the surface
   is read, **Then** their cost is reported as a separate total, not averaged
   into the merged figure.
3. **Given** PRs whose ground-truth state has never been refreshed, **When** the
   surface is read, **Then** those are reported as an explicit unknown bucket
   and are excluded from both rates.
4. **Given** usage was never reported for some tasks, **When** the surface is
   read, **Then** the figures state how many tasks the number is based on, so a
   partial sample is never mistaken for a complete one.

---

### User Story 5 — See it without asking (Priority: P5)

The owner opens the console and, without running a query, sees whether the loop
is healthy: is it running, is it self-healing, is it converging, what is it
costing.

**Why this priority**: Pure surfacing. Every number it shows is produced by
US1–US4; shipping it earlier would display nothing.

**Independent Test**: Load the console in a browser against an instance with
seeded history and confirm each of the five core metrics is visible without
navigating away from the overview, and that a metric with no data reads as
unknown rather than as zero.

**Acceptance Scenarios**:

1. **Given** an instance with recorded history, **When** the owner loads the
   console overview in a browser, **Then** a single loop-health strip shows the
   five core metrics.
2. **Given** permanent usage history spanning several months, **When** the owner
   opens the usage view, **Then** a trend over that full period is rendered, not
   only the retention window.
3. **Given** a metric has no underlying data, **When** the owner views it,
   **Then** it renders as unknown and is visually distinct from a true zero.

---

### User Story 6 — Know whether the pre-execution size estimate is worth anything (Priority: P6)

devclaw already forms an opinion about how big a piece of work is **before** it
runs — the intake readiness gate returns an assessed unit-of-work count on the
same one-shot call as the readiness verdict, and the filer states a claim of his
own. Both are surfaced once to a human and then discarded. Nobody has ever
checked whether either number predicts what the work actually took, so the
estimate can be neither trusted nor deleted.

**Why this priority**: It is not a north-star metric and nothing is wedged
without it, so it ranks below the four health numbers and the console. It is
placed here rather than dropped because it is the only path to a decision the
repo cannot otherwise make: the sizing axis is either a budget input worth
promoting or theatre worth deleting, and today there is no evidence either way.
Its non-cost columns are buildable independently of US1–US5; only its cost
column waits on US3.

**Independent Test**: Close a goal that was created from a graded issue carrying
both a filer claim and a grader assessment, then read the calibration surface
and confirm one row reports both predictions alongside the actual dispatch count
and the goal's lifetime done-gate rounds.

**Acceptance Scenarios**:

1. **Given** a goal created from an issue whose grading recorded an assessed
   unit count, **When** the goal reaches a terminal outcome, **Then** its
   convergence record carries the assessed count, the filer's claimed count, the
   number of dispatches the goal actually took, and its lifetime done-gate
   rounds.
2. **Given** a goal created without any graded issue behind it, **When** it
   closes, **Then** the prediction fields read as unknown and the actual counts
   are still recorded — an unpredicted goal is measurable, it just contributes
   nothing to the correlation.
3. **Given** a set of closed goals with recorded predictions, **When** a caller
   reads the calibration surface, **Then** it reports the agreement between each
   prediction and the actual dispatch count, together with the number of goals
   the figure is based on.
4. **Given** fewer closed-and-predicted goals than the reporting threshold,
   **When** the surface is read, **Then** it reports the correlation as not yet
   determinable and names how many more goals are needed, rather than reporting
   a figure from a sample too small to mean anything.
5. **Given** a goal whose prediction and actuals disagree sharply, **When** the
   goal closes, **Then** nothing in the loop's behaviour changes as a result —
   the record is written, no dispatch is refused, delayed or re-shaped.

---

### Edge Cases

- **The loop is idle for two different reasons in one window** (a goal parked on
  a merge conflict while the backlog is also empty). Attribution must be
  deterministic and total to the elapsed time — never double-count.
- **The cause changes mid-window** (a pause expires, then the backlog empties).
  Attribution must follow the change at tick granularity rather than assign the
  whole window to whichever cause was observed first.
- **The cause flaps between two ticks** (a block appears and self-heals inside
  one interval). Accepted as unobserved by FR-004a — the interval is attributed
  to whatever the tick sees. The self-heal itself is still counted by US2, which
  reads the problems catalog, not the idle sampler.
- **No usage is reported at all** — see Assumptions. Every derived figure must
  degrade to *unknown*, and the surface must state how many records it is based
  on, so an empty sample is never displayed as a healthy zero.
- **A task settles while its PR state is stale** — cost-per-outcome must place it
  in the unknown bucket, never guess a state from the presence of a PR URL.
- **The database is fresh** (no history at all). Every metric reads unknown; no
  division by zero, no misleading 0% or 100%.
- **A retried task** — cost is the sum of attempts; a task that succeeded on
  attempt 3 cost three attempts, and reporting only the last one understates.
- **A goal is steered mid-flight** — the prediction was made against a different
  ask than the one that closed. The record must keep the prediction as filed and
  mark that steering intervened, so a re-scoped goal is not scored as a bad
  estimate.
- **A goal is cancelled rather than achieved** — its actuals are real and must be
  recorded, but a cancelled goal's dispatch count is a floor, not a cost, and
  must not be averaged in with achieved goals.
- **A goal references several graded issues** — the predictions are per-issue and
  the actuals are per-goal. The record must state which predictions it is the
  sum of, rather than silently comparing one issue's estimate to a whole goal's
  work.

## Requirements *(mandatory)*

### Functional Requirements

**Idle attribution (US1)**

- **FR-001**: The system MUST record, for every period in which no work is in
  flight, a machine-readable cause for that idleness.
- **FR-002**: Where the cause is an existing block, the system MUST reuse the
  established `blocked_kind` vocabulary verbatim (`needs_answer`, `bug`,
  `lost_ref`, `dispatch_cap`, `mechanical:merge_failed`, `mechanical:ci`,
  `mechanical:env`, `mechanical:corrupt_doc`, `mechanical:prep`,
  `donegate_churn`) and MUST NOT introduce a parallel name for any of them.
- **FR-003**: The system MUST introduce a vocabulary for the currently-unnamed
  case of "stopped because there is nothing to do", with these members:
  `empty_backlog`, `no_goal_armed`, `all_planned_done`, `window_closed`,
  `paused`.
- **FR-004**: Idle MUST be attributed by SAMPLING on the existing heartbeat:
  each tick attributes the interval since the previous tick to the single cause
  it observes at that moment. Attribution MUST therefore total the elapsed idle
  time at tick resolution — no interval unattributed, none counted twice.
- **FR-004a**: Attribution MUST have exactly ONE write point, on the heartbeat
  path (Constitution IV). A cause that both appears and clears between two
  consecutive ticks is accepted as unobserved; the feature MUST NOT add
  transition-time write points across other layers to catch it.
- **FR-005**: Every idle cause MUST carry exactly one of three responsibility
  buckets, and only the first MUST count toward any rollup presented as
  devclaw's own health:
  - **devclaw-caused** — `bug`, `lost_ref`, `dispatch_cap`, `donegate_churn`,
    and every `mechanical:*` block. devclaw stopped and it is devclaw's failure.
  - **owner's turn** — `needs_answer`, `no_goal_armed`, and any open Problem
    awaiting a decision. Visible and separately reportable, so the owner can see
    his own latency, but never scored against devclaw.
  - **no work available** — `empty_backlog`, `all_planned_done`,
    `window_closed`, `paused`. Nothing was there to do, or an external limit
    applied; not a failure of any party.
- **FR-005a**: The bucket MUST be derived from the cause, not stored
  independently of it, so a cause can never carry two different buckets in two
  places.
- **FR-005b**: The system MUST report a single headline **not-stuck rate**: the
  fraction of a window spent either working or idle for a non-devclaw reason
  (owner's turn / no work available). Only devclaw-caused idle reduces it. It
  MUST be presented alongside the three-bucket breakdown it is derived from,
  never as a replacement for it, and MUST read as unknown for a window with no
  recorded time rather than as 100%.
- **FR-006**: Idle attribution MUST NOT make a cognition call. The zero-token
  idle guard (Constitution III) holds unchanged.

**Self-heal rate (US2)**

- **FR-007**: The system MUST report an aggregate self-heal rate derived from the
  existing recovered/terminal counters, together with the raw counts it is
  computed from.
- **FR-008**: With no underlying records, the rate MUST report as unknown, never
  as zero or as complete success.

**Durable usage (US3)**

- **FR-009**: Per-run token and cost figures MUST be extracted at settle time
  into a permanent record that transcript retention never deletes.
- **FR-010**: The transcript retention schedule MUST remain unchanged — this
  feature does not extend it, and does not introduce a second archive store.
- **FR-010a**: On introduction, the permanent record MUST be backfilled from
  whatever transcripts retention has not yet pruned. Tasks whose transcripts are
  already gone MUST read as unknown, never as zero — the backfill boundary must
  be legible as missing data, not as a period of free operation.
- **FR-011**: A run that reported no usage MUST be recorded as *unknown*, never
  as zero. Every derived total MUST carry the count of records it is based on.
- **FR-012**: Usage MUST roll up mechanically from run → task (summing retries)
  → goal → project → instance, using existing identity links.
- **FR-013**: Rollups MUST be computed without any cognition call and without
  introducing a second writer to task state (Constitution IV).

**Cost per outcome (US4)**

- **FR-014**: The system MUST report cost attributable to merged work separately
  from cost attributable to runs that shipped nothing.
- **FR-014a**: Merged-work cost MUST be reported SEGMENTED BY DELIVERY SHAPE —
  cost per merged **goal** (whose cumulative PR carries many increments) and
  cost per merged **standalone PR** (one task, one PR) as two distinct figures,
  each with the count it is based on. The two MUST NOT be averaged into a single
  blended figure: they are different units, and blending them makes the metric
  move with the delivery mix rather than with cost.
- **FR-015**: PRs whose ground-truth state is unknown or stale MUST form an
  explicit third bucket, excluded from both rates — a state MUST NOT be inferred
  from the presence of a PR reference.

**Surfacing (US5)**

- **FR-016**: The console MUST present the five core metrics — idle by cause
  (led by the not-stuck rate of FR-005b), self-heal rate, clean-cycle rate,
  first-pass rate, and cost per merged goal / standalone PR — with the two
  already-built metrics read from their existing sources, not recomputed.
- **FR-017**: The console MUST render a usage trend across the full retained
  history, not only the transcript-retention window.
- **FR-018**: Any metric lacking data MUST render as unknown, visually distinct
  from a real zero.

**Estimate calibration (US6)**

- **FR-021**: The grader's assessed unit-of-work count MUST be persisted in a
  machine-readable form at grading time. Today it survives only as prose inside
  a mirror comment, which is why no comparison has ever been possible. The
  filer's claim MUST continue to be the recorded claim (spec 012 FR-010b); this
  requirement adds a second, separately-labelled field and changes neither the
  claim nor the readiness verdict.
- **FR-022**: At a goal's terminal transition the system MUST record, in the
  SAME row as the existing convergence outcome and lifetime round count, the
  predictions that were made about it and the work it actually took: the
  grader's assessed units, the filer's claimed units, and the number of
  dispatches the goal consumed. It MUST NOT introduce a second ledger, a second
  write point, or a second terminal-transition hook.
- **FR-023**: Cost per closed goal MUST be joined from the permanent usage
  record of US3 rather than re-derived. Until US3 ships, the cost column reads
  unknown; the rest of the record MUST NOT wait on it.
- **FR-024**: An absent prediction MUST read as unknown, never as zero, and a
  goal with no prediction MUST still record its actuals (FR-011's rule applied
  to this record).
- **FR-025**: The calibration surface MUST report the sample size it is based
  on, and MUST report the correlation as not-yet-determinable below a stated
  minimum sample rather than emit a figure from a handful of goals.
- **FR-026**: This feature MUST NOT act on the comparison. No dispatch may be
  refused, delayed, split or re-budgeted because of a prediction, and no new
  model call may be made to produce, refine or explain one. The estimate already
  exists; this requirement records it and stops. Acting on it is a separate
  decision, to be taken on the evidence this produces.
- **FR-027**: The record MUST distinguish achieved from cancelled outcomes and
  MUST mark whether steering intervened between the prediction and the close, so
  a re-scoped goal is excluded from the correlation rather than scored as a bad
  estimate.

**Operational obligations**

- **FR-019**: Because this changes persisted state shape, it MUST ship a doctor
  check with a seeded-fault test (spec 016 FR-014).
- **FR-020**: It MUST pin one tripwire-class invariant: *absent usage is never
  rendered as zero* — at every layer that reads or displays it.

### Key Entities

- **Idle attribution record**: an idle period with a start, an end, and exactly
  one cause drawn from either the existing block vocabulary or the new
  nothing-to-do vocabulary. Its responsibility bucket — devclaw-caused /
  owner's turn / no work available — is derived from the cause (FR-005a), never
  recorded alongside it.
- **Task usage record**: one permanent row per task attempt — identity links to
  its task, goal and project, its kind, its token counts, its cost figure, and a
  flag distinguishing *reported* from *not reported*.
- **Cost-per-outcome view**: a projection joining usage records to ground-truth
  PR state, partitioned into merged / shipped-nothing / unknown, with the merged
  partition further segmented by delivery shape (goal-cumulative vs standalone)
  per FR-014a.
- **Goal calibration record**: the EXISTING per-terminal-goal convergence record,
  extended in place. Alongside the outcome and lifetime done-gate rounds it
  already holds, it gains what was predicted about the goal (grader assessment,
  filer claim), what the goal actually consumed (dispatch count, and cost once
  US3 lands), and whether steering intervened. It is a new set of columns on one
  existing row, not a new store.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any past window, the owner can determine why the loop was not
  running without reading logs — every idle minute carries a cause, and causes
  sum to the window's idle time.
- **SC-002**: Every idle minute resolves to exactly one of three
  responsibilities — devclaw's failure, the owner's turn, or no work available —
  so the owner's own response latency and an empty backlog never move devclaw's
  health score, while both remain separately visible.
- **SC-003**: Cost and token figures remain readable for history older than the
  transcript retention window; a trend spanning more than that window can be
  drawn.
- **SC-004**: The owner can state what an average merged goal cost, what an
  average standalone merged PR cost, and what was spent on runs that shipped
  nothing — as separate figures that do not move when the delivery mix shifts.
- **SC-005**: No metric in this feature ever displays a fabricated zero — an
  absent measurement is always distinguishable from a measured zero, at every
  layer.
- **SC-006**: Permanent metric storage stays under 1% of total database growth,
  with transcripts remaining the dominant term.
- **SC-007**: Adding these metrics costs zero additional model calls — an idle
  instance still makes none (Constitution III).
- **SC-008**: The five core metrics are visible from the console overview
  without running a query or navigating away.
- **SC-009**: For any closed goal, the owner can state what was predicted about
  its size before it ran and what it actually took, from one record, without
  reading an issue comment.
- **SC-010**: After a stated minimum number of closed-and-predicted goals, the
  owner can decide on evidence whether the pre-execution size estimate is
  predictive enough to become a budget input or should be deleted — a decision
  that is unmakeable today at any sample size.

## Out of Scope — rejected, with reasons

Recorded here as direction memory (the spec is the only home for it):

- **A single "% non-idle" utilization metric.** A loop 100% busy failing is worse
  than one idle at 40%, so raw utilization cannot be read as health. The repo
  already ruled this correctly once: idle cycles are deliberately excluded from
  the clean-cycle rate. Idle is measured **by cause** instead.
  **Not to be confused with the not-stuck rate (FR-005b)**, which IS in scope:
  utilization punishes legitimate idleness, whereas the not-stuck rate excludes
  it by construction — a loop with an empty backlog scores healthy, a wedged
  loop does not. The rejected metric counts *working time*; the accepted one
  counts *absence of devclaw's own stoppage*.
- **Problem rate as a percentage.** The problems catalog is a gatherer-signal
  readout, not a backlog; the canonical store of intent is GitHub Issues. It
  stays a list, with recurrence-across-nights as its signal.
- **Average tokens per run.** The mean hides the distribution, and the
  distribution is the story — a few large runs dominate. Superseded by cost per
  outcome; if a per-run figure is ever wanted, it is p50/p90, not a mean.
- **Monthly archive files.** Rejected in favour of the permanent thin projection:
  archives still cannot draw a trend without rehydration, add a failure mode that
  fails silently (violating Constitution VI), and create a second store with no
  schema guarantee that nothing queries. If volume ever justifies it, the
  standard answer is downsampling old rows in place, not a second store.
- **A task-complexity classifier or taxonomy.** Judgment belongs to the agent,
  not to Python (Constitution IX). The mechanical proxy falls out of this
  feature for free once US3 and US4 land: tokens spent × rounds-to-close ×
  change-size span, reusing the single existing definition of "what changed".
  Measured after the fact, never labelled before.
  **Not to be confused with US6**, which builds no classifier and adds no
  judgment: the pre-execution estimate it records is one devclaw already
  computes and already throws away. US6 measures that existing estimate against
  the after-the-fact actuals; it does not introduce a new label, a new taxonomy,
  or a new model call. The entry above rejects *manufacturing* a complexity
  judgment. US6 is the experiment that decides whether the one already being
  manufactured is worth keeping.
- **Acting on the size estimate — refusing, splitting, or re-budgeting a goal
  because of it.** Deliberately excluded by FR-026. Three reasons. First, there
  is no evidence the estimate is predictive, and a gate built on an uncalibrated
  number is worse than no gate. Second, refusal is already owned at a different
  seam: the admission lint refuses on *capability* (a clause the sandbox cannot
  satisfy), and size is not capability — a large goal is legitimate work, not an
  inadmissible one. Third, if the estimate ever does earn a consequence, the
  legitimate one is a **budget** (Constitution IX names money as software's
  domain) or a route to **slicing**, never a refusal. Revisit on the evidence
  SC-010 produces, not before.
- **Rebuilding the existing usage, evals, or problems views.** They work. This
  feature adds to them.

## Assumptions

- **Idle is sampled at the existing heartbeat cadence** (ruled, see
  Clarifications). Attribution happens on the tick that already runs, so
  resolution is one tick and the cost is zero extra model calls. The periods
  being diagnosed are hours long, so tick resolution is far finer than the
  question requires.
- **The already-built metrics are read, not recomputed.** Clean-cycle rate and
  first-pass rate keep their current definitions and sources; this feature only
  surfaces them alongside the new ones.
- **Cost figures are an API-equivalent estimate, not a bill.** Under the
  OAuth-only invariant (Constitution I) no dollar cost is reported by the
  provider. Token counts are the ground truth; any currency figure is a derived
  estimate and must be labelled as one, matching what the existing usage view
  already does.
- **Existing history is backfilled where it survives** (ruled, see
  Clarifications; requirement at FR-010a). History already deleted by retention
  is unrecoverable and is reported as unknown rather than as zero.
- **`window_closed` is expected to be rare.** The owner has disabled the run
  window for 24/7 operation; the member exists so the cause is nameable if the
  window is ever re-enabled.
- **The prediction exists and is already paid for.** The intake readiness gate
  returns its unit-of-work assessment on the same one-shot call as the readiness
  verdict, so US6 adds no model call — it adds a column and a write at a
  transition that already happens. The filer's claim is likewise already durable
  in the issue body.
- **The convergence ledger is the right home.** A per-terminal-goal record
  written at the close/cancel transition already exists and already carries the
  outcome and the lifetime done-gate round count. Extending it keeps one writer,
  one write point and one row per goal; a separate calibration store would
  duplicate all three for no gain.
- **Both predictors are recorded, neither is privileged.** The filer's claim and
  the grader's assessment are different predictors and may have different
  accuracy. Recording only one would presume the answer to the question the
  story exists to ask.

## Known unverified assumption — RISK

**It has not been confirmed on the live instance that the worker agent actually
reports its token usage.** The collection path exists and is best-effort by
design; the existing usage view already tolerates absence. If the agent reports
nothing in production, then US4 and the complexity proxy will measure nothing
until a different measurement source is found — US1, US2 and US3 are unaffected.

The check that settles it: read the problems catalog for context-tripwire
records and inspect recent settled task payloads for a usage block. The owner
declined to run it during the authoring session; it should be run before US4 is
planned.

This is precisely why FR-011 and SC-005 are load-bearing: the feature must be
honest about an empty sample rather than render it as a healthy zero.
