# Feature Specification: Unattended-Week Operation

**Feature Branch**: `025-unattended-operation`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "Unattended-week operation: merge-on-close, post-merge self-deploy, and quiet-mode notifications. The operator will be away for a full week; devclaw must work a pre-filed queue of goals end-to-end with zero human touches — PRs flowing through the gates and merging into each repo's default branch, small increments, one goal at a time, no failure class that requires a human."

## Context

Ruled by Denys 2026-08-29. Today a goal's cumulative PR (goal-branch delivery,
one PR per goal, #486) stays open forever waiting for a human merge: auto-merge
was deliberately deleted in #641 with the rationale "in companion mode a human
reviews and merges — machinery that merges without one is compensating for an
absent reviewer, not adding a capability." Next week the reviewer is
*deliberately* absent, and the operator has explicitly authorized the reversal
— at exactly one seam. Everything else the unattended week needs already
exists: per-project lane serialization, the done-gate, pause-and-resume for
quota/auth, per-project sandbox sizing (spec 020), the brief budget (#729),
and the slice-guard fix (#728, in flight).

Three prior decisions this spec explicitly supersedes or leans on:

- **#641 (merge doctrine)** — reversed at one seam only: merge happens as a
  consequence of the done-gate closing the goal, never anywhere else. The
  #486 invariant (the PR stays open for the entire life of the goal) is
  untouched; merge-on-close fires strictly after close.
- **2026-07-19 finance-sentry pin** ("merge/deploy automation OFF pending
  explicit Denys approval") — the merge half of the pin is lifted by this
  ruling; merge-on-close applies to all four lifekit repos (devclaw,
  finance-sentry, lifekit-dashboard, lifekit-common). The deploy half of the
  pin stays: nothing in this spec deploys finance-sentry.
- **Constitution Principle V** rests `trust` mode partly on "the human reviews
  every PR." With merge-on-close that clause is no longer true. This spec
  amends the constitution in the same arc (see Constitution Impact).

## Clarifications

### Session 2026-08-29 (with Denys)

- Q: Which repos get merge-on-close? → A: All four lifekit repos, including
  finance-sentry (merge half of the 2026-07-19 pin explicitly lifted).
- Q: Self-deploy during the unattended week, or frozen? → A: Self-deploy,
  with quiescence gate, health probe, and automatic rollback.
- Q: Notification policy while away? → A: Only instance-dead events ping;
  everything else recorded, surfaced on return.
- Q: Pre-merge review authority (FR-006)? → A: Done-gate only; human review
  moves post-merge; pre-merge cumulative review rejected (reliability).
- Q: Permanence (FR-008)? → A: Permanent standing behavior, not
  armed-window-only; two close paths rejected.
- Q: Parked goal mid-queue (FR-015)? → A: Skip-over — the lane never holds
  for a parked goal; hold-the-lane rejected.
- Q: Merge conflicts / dependency collisions at close? → A: Fixed
  automatically by devclaw dispatching a conflict-resolution increment
  through its own pipeline (FR-017) — one bounded attempt, verify-gated,
  done-gate re-confirmed; a side-channel ops agent was considered and
  rejected (the normal pipeline already carries the sandbox, the verify
  gate, and the change-span accounting a resolution needs).
- Operator planning principle, recorded: "we plan goals such that they
  should not fail" — failure is designed out at filing time (intake grading,
  small pre-verified scopes); runtime parking is the backstop, not the plan.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merge-on-close (Priority: P1)

The operator files a queue of well-planned goals and leaves. Each goal runs
its increments, proposes done, and the done-gate confirms `achieved` against
the firmed `done_when`. At that moment — gates all green, goal complete,
nothing left to accumulate — devclaw squash-merges the goal's cumulative PR
into the repository's default branch and deletes the goal branch. The next
queued goal for that project then starts from a default branch that already
contains its predecessor's work.

A merge that cannot complete (conflict with the base, API error, branch
protection refusal) fails LOUD: the goal parks with a structured blocked kind
and an owner-facing reason, because silently leaving the PR open would make
the next goal in the lane branch from a default branch missing the
predecessor's work — a silent-degradation class, not an inconvenience.

**Why this priority**: This is the single missing piece of the end-to-end
mechanism. Without it, a week of sequential goals per repo deadlocks by
design: PRs pile up unmerged, dispatch caps fire ("review the open PRs" — the
#1 recurring wedge class, 3 of the last 10 active nights), and successor
goals fork from a stale main.

**Independent Test**: Can be fully tested with a stubbed forge: drive one
goal to a confirmed-`achieved` close and assert the merge call fires with the
squash strategy exactly once, after the close transition; drive a merge
failure and assert the goal parks with the named blocked kind instead of
closing quietly.

**Acceptance Scenarios**:

1. **Given** a goal whose done-gate evaluation returns `achieved` and whose
   cumulative PR is open and mergeable, **When** the goal closes, **Then** the
   PR is squash-merged into the default branch, the goal branch is deleted,
   and the delivery record notes the merge (merged SHA, timestamp).
2. **Given** a goal whose done-gate returns `achieved` but whose PR is
   CONFLICTING with its base, **When** the close path runs, **Then** ONE
   conflict-resolution increment is dispatched through the normal pipeline
   (worker resolves onto current default-branch head, verify gate re-runs,
   done-gate re-confirms), the merge is re-attempted, and only a second
   conflict parks the goal blocked with a kind naming the merge failure and
   the PR URL — with the tick loop serving other goals throughout.
3. **Given** a goal that is still mid-flight (increments running, no
   `achieved` verdict), **When** any tick runs, **Then** no merge is ever
   attempted — the PR stays open for the entire life of the goal (#486).
4. **Given** a parked merge failure whose conflict the operator later resolves
   out-of-band, **When** the operator resumes the goal, **Then** the close
   path re-attempts the same merge without re-running the done-gate.
5. **Given** two queued goals on one project, **When** the first closes and
   merges, **Then** the second starts from a workspace synced to the
   post-merge default branch head.

---

### User Story 2 - Post-merge self-deploy with probe and rollback (Priority: P2)

For the devclaw repository only: after a successful merge-on-close, the
running instance redeploys itself onto the merged default branch (spec 005
machinery). The deploy is gated: it waits until the instance is quiescent (no
task in flight anywhere), then deploys, then runs a health probe against the
new instance. A probe failure automatically rolls back to the previously
running version and parks a record of the failed deploy; the instance never
stays down. One bad merge must not kill the unattended week.

**Why this priority**: The operator chose self-deploy for the week — devclaw's
own fixes (built by its own goals) should reach the live instance without a
human. It is P2 because the week functions without it (merged devclaw work
simply waits for a redeploy), while a botched unattended deploy is the single
highest-blast-radius failure available.

**Independent Test**: With a stubbed deploy runner: a devclaw-repo goal close
triggers deploy only after in-flight count reaches zero; a probe failure
triggers exactly one rollback to the prior version and the instance reports
healthy on the prior version; a probe success records the new version as
current.

**Acceptance Scenarios**:

1. **Given** a devclaw-repo goal merges on close while another project's task
   is in flight, **When** the deploy step evaluates, **Then** it waits and
   re-checks rather than deploying under a running task, and deploys once the
   instance is quiescent.
2. **Given** a deploy whose post-deploy health probe fails, **When** the
   rollback fires, **Then** the previously running version is restored, the
   instance answers its health surface, and the failed deploy is recorded
   loudly for the operator's return.
3. **Given** a deploy whose probe succeeds, **When** the next devclaw goal
   runs, **Then** it runs against the newly deployed version.
4. **Given** a non-devclaw repo goal closing and merging, **When** the close
   completes, **Then** no deploy of any kind fires (the finance-sentry deploy
   pin holds; product repos are out of deploy scope).

---

### User Story 3 - Quiet mode for owner notifications (Priority: P3)

The operator arms quiet mode before leaving. While armed, only instance-dead
events ping the owner — an auth failure that pause-and-resume cannot heal
(token revoked, or re-probes exhausted without recovery), i.e. the class
where the whole week dies silently otherwise. Every other ping class (parks,
churn brakes, failed merges, cycle reports, delivery notices) is recorded
with its timestamp and surfaced on return as a catch-up digest, but not sent.
Disarming quiet mode restores normal notification behavior.

**Why this priority**: The operator's explicit ruling ("only instance-dead
events"). It is P3 because the week functions with noisy pings — they are
ignorable — while an unsent instance-dead ping is the one suppression that
would be catastrophic, so the default (not armed) changes nothing.

**Independent Test**: With a recording notifier: arm quiet mode, drive one
event of each ping class, assert exactly the instance-dead class was sent and
all others were recorded-not-sent; disarm and assert normal routing resumes;
assert the recorded backlog is readable as a digest.

**Acceptance Scenarios**:

1. **Given** quiet mode armed, **When** a goal parks on a merge conflict,
   **Then** no ping is sent and the event appears in the recorded backlog.
2. **Given** quiet mode armed, **When** an auth pause exhausts its re-probes
   without recovery, **Then** the owner IS pinged with the actionable
   re-login message.
3. **Given** quiet mode armed with an expiry date, **When** the date passes,
   **Then** quiet mode disarms itself and normal pings resume — a forgotten
   toggle does not mute the instance forever.
4. **Given** quiet mode disarmed on return, **When** the operator asks for
   status, **Then** the suppressed-ping backlog is available in one read.

---

### Edge Cases

- Done-gate confirms `achieved` but the PR was already merged manually (the
  operator merged from a phone): merge-on-close observes the PR is merged,
  treats it as success, and closes normally — idempotent, never a crash.
- The PR is closed-without-merge at close time (a human rejected it): the
  goal parks loudly — closing the goal as achieved while its work is
  discarded would be a silent lie.
- Merge succeeds but the goal-branch delete fails: the merge stands, the
  stale branch is reported, the goal still closes — branch hygiene never
  blocks completion.
- The forge is unreachable at close time: the close path retries within its
  existing bounded-retry conventions, then parks blocked; a network blip is
  not a conflict.
- Deploy quiescence never arrives (a task is always in flight): the deploy
  attempt expires after a bounded wait and is re-attempted at the next
  devclaw-repo close or operator resume — it never wedges the tick loop, and
  the merged-but-not-deployed state is recorded.
- Rollback itself fails (prior version won't start): this IS an
  instance-dead event — it pings through quiet mode.
- Two devclaw-repo goals queued back-to-back: the second's dispatch waits for
  the first's deploy step to settle (success or rollback), so a goal never
  runs against an instance mid-deploy.
- Quiet mode armed while a ping is already in flight: in-flight sends
  complete; suppression applies from arming forward.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When, and only when, the goal-level done-gate confirms
  `achieved` and the goal transitions to done, the system MUST squash-merge
  the goal's cumulative PR into the repository's default branch. No other
  code path may merge a goal PR.
- **FR-002**: A merge attempt that fails MUST first exhaust the bounded
  self-heal of FR-017 (conflict class) or the bounded retry conventions
  (transient forge errors) before parking; a failure that survives those
  MUST park the goal in a blocked state with a structured blocked kind and
  an owner-facing reason naming the PR. The goal MUST NOT close as achieved
  with its PR unmerged, and the failure MUST NOT wedge the tick loop or
  affect other goals.
- **FR-003**: A goal already parked on a merge failure MUST re-attempt the
  same merge on operator resume without re-running the done-gate (the
  achieved verdict stands; only the merge is retried).
- **FR-004**: Merge-on-close MUST be idempotent against external action: an
  already-merged PR at close time is success; a closed-unmerged PR parks the
  goal loudly.
- **FR-005**: After merge, the workspace for the project MUST be synced so
  the next queued goal branches from the post-merge default branch head.
- **FR-006**: The done-gate's grounded evaluation against the firmed
  `done_when` is the SOLE pre-merge authority — no additional review gate is
  consulted at close (ruled by Denys 2026-08-29; see Clarifications). The
  human review moves post-merge; revert remains the remedy. Rationale for the
  rejected alternative: a pre-merge cumulative-diff review re-introduces the
  system's least reliable call class (review crashes on oversized diffs,
  ×117 in the problems catalog) at the exact moment the operator cannot
  unpark it.
- **FR-007**: Merge-on-close MUST apply to goals on all four lifekit
  repositories; the 2026-07-19 finance-sentry pin is lifted for merge only —
  no product repository is deployed by this feature.
- **FR-008**: Merge-on-close is PERMANENT standing behavior for these
  repositories from the moment it ships — not gated on quiet mode or an
  unattended window (ruled by Denys 2026-08-29). The operator's review moves
  post-merge as the standing workflow. Rejected alternative: an armed-only
  mode was declined to avoid maintaining two close paths with different
  merge semantics.
- **FR-009**: After a successful merge-on-close of a devclaw-repository goal,
  the system MUST redeploy the instance onto the merged default branch,
  gated on instance quiescence (zero in-flight tasks), with a bounded wait
  that expires loudly rather than blocking forever.
- **FR-010**: Every self-deploy MUST run a post-deploy health probe; a probe
  failure MUST trigger exactly one automatic rollback to the previously
  running version, and a rollback failure MUST be classified as an
  instance-dead event.
- **FR-011**: The deploy step MUST record every outcome (deployed version,
  probe result, rollback if any) durably enough to survive the deploy itself,
  and a merged-but-not-yet-deployed state MUST be visible to the operator.
- **FR-012**: The system MUST provide an operator verb to arm and disarm
  quiet mode, with an optional expiry timestamp; expiry disarms
  automatically.
- **FR-013**: While quiet mode is armed, only instance-dead events (auth
  failure that pause-and-resume cannot heal; rollback failure per FR-010)
  are sent to the owner; all other ping classes are recorded with timestamps
  and not sent.
- **FR-014**: Suppressed pings MUST be readable on return as a single
  catch-up surface, in order, without loss.
- **FR-015**: A goal parked mid-queue MUST NOT hold its project's lane: the
  next queued goal for that project starts, and the parked goal waits for
  the operator (ruled by Denys 2026-08-29 — skip-over). The planning
  discipline files goals independent of one another; a successor that did
  depend on the parked goal's unmerged work fails its own done-gate loudly
  rather than shipping wrong. Rejected alternative: holding the lane was
  declined because one conflict would idle a repository for the whole week.
- **FR-016**: All merge, deploy, and quiet-mode behavior MUST cost zero
  cognition calls on idle ticks — the zero-token idle guard is untouched.
- **FR-017**: A merge that fails on CONFLICT (or an equivalent
  integration-blocking state of the goal branch, e.g. a dependency/lockfile
  collision with the default branch) MUST trigger ONE self-heal attempt
  before parking: the system dispatches a conflict-resolution increment
  through the normal execution pipeline — a sandboxed worker updates the
  goal branch onto the current default branch head and resolves the
  conflicts, the always-hard verify gate runs on the result, and the
  done-gate re-confirms `achieved` against the post-resolution state —
  then the merge is re-attempted. Exactly one self-heal attempt per close;
  a second conflict parks per FR-002 (ruled by Denys 2026-08-29: conflicts
  are fixed automatically, by devclaw's own dispatch, not by a human or a
  side-channel agent).

### Key Entities

- **Merge outcome**: the recorded result of a merge-on-close attempt —
  goal, PR, strategy, merged SHA or failure reason, timestamp. One per close
  attempt; consumed by delivery history and the catch-up digest.
- **Deploy record**: the recorded result of one self-deploy — version
  deployed from, version deployed to, probe verdict, rollback verdict,
  timestamps. The current-version pointer survives restarts.
- **Quiet-mode state**: armed/disarmed, optional expiry, armed-by, armed-at.
  One instance-wide record, not per-goal.
- **Suppressed ping**: a notification that would have been sent — class,
  goal ref, message, timestamp — retained for the catch-up surface.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A well-planned goal that reaches a confirmed `achieved` verdict
  lands on its repository's default branch with zero human touches, within
  one heartbeat interval of the verdict.
- **SC-002**: A queue of at least 3 goals on one repository completes
  sequentially with zero human touches, each successor building on its
  predecessor's merged work.
- **SC-003**: Over a simulated unattended week (the stubbed e2e suite), no
  failure of any single goal — merge conflict, gate crash, deploy probe
  failure — prevents other projects' goals from progressing, and the
  instance answers its health surface throughout.
- **SC-004**: With quiet mode armed, the owner receives pings for 100% of
  instance-dead events and 0% of any other event class, and 100% of
  suppressed events are readable on return.
- **SC-005**: A failed self-deploy ends with the instance healthy on the
  prior version in under 5 minutes, without operator action.

## Assumptions

- The forge credential available to the delivery layer (host-side `gh`) has
  permission to merge PRs on all four repos; the sandbox continues to carry
  no forge credential.
- Branch protection on the four repos either permits the credential to merge
  or will be adjusted by the operator before the week starts; the spec treats
  a protection refusal as an ordinary loud merge failure, not a class to
  auto-negotiate.
- The week's goals are filed by the operator with intake-graded, full-scope
  `done_when`s and pre-verified established facts ("we plan them such that
  they should not fail" — failure is designed out at planning time; runtime
  parking is the backstop, not the plan).
- Spec 005's deploy machinery is the substrate for US2; this spec adds
  gating, probe, and rollback around it, not a new deploy path.
- The done-gate, verify gate, and test-integrity gate remain always-hard and
  unchanged; this spec adds no new gate consultation semantics beyond
  whatever FR-006's answer selects.
- Cycle reports continue to be generated during quiet mode (they are part of
  the recorded surface); only their outbound send is suppressed.

## Out of Scope

- Deploying any product repository (finance-sentry, lifekit-dashboard,
  lifekit-common) — the 2026-07-19 deploy pin stands.
- Autonomous issue claim/dispatch (spec 007) — the week's queue is filed by
  the operator, not self-selected.
- Changing how goals are planned, graded, or sliced — planning quality is
  process, enforced at filing time.
- Retrying merges on a schedule — a parked merge failure waits for the
  operator (or FR-015's answer).
- Any change to the strictness dial's per-increment gate semantics beyond
  what FR-006's answer explicitly selects.

## Constitution Impact

Principle V currently justifies `trust` mode in part with "the human reviews
every PR and the goal-level done-gate re-catches its findings." Under
merge-on-close (permanent, per FR-008) the human review happens post-merge,
so this spec MUST amend Principle V's rationale in the same arc to: the
goal-level done-gate's grounded evaluation is the merge authority; the human
reviews merged work after the fact and revert is the remedy. Principle VI
(loud failure) and Principle III (zero-token idle) are load-bearing
constraints on the design and are unchanged. No other principle is touched.
