# Feature Specification: An environment gap is filed when the pipeline says it is filed

**Feature Branch**: `038-env-deficiency-filing-honesty`

**Created**: 2026-09-07

**Status**: SHIPPED — US1 and US2 implemented 2026-09-07, plus the T010 post-landing correction that made FR-007 hold on the two failure exits the doorway cannot see. Every user story in this spec is built; the only carried item is the pre-existing ledger drift recorded under "Known gap (not this spec's)".

**Input**: lifekit-hq/devclaw issue #818 — "Worker env deficiency claims 'filed as devclaw work' but no issue was created"

## Why (context the requirements hang off)

On 2026-09-03 17:01 UTC task `7a324df0-b791-4d7c-b96f-efcd0de9f8e8` (goal
`fs-431-hygiene-sentinels-2026-08-31`) settled with a worker-reported
environment deficiency — no `NODE_AUTH_TOKEN` for GitHub Packages — and the
settle text told the operator:

> Owned by devclaw: the project holds until its environment changes, **and the
> gap is filed as devclaw work.**

`DEVCLAW_SELF_REPO=lifekit-hq/devclaw` was set in the running container. No
issue existed on `lifekit-hq/devclaw` a day later. The goal log carried the
hold lines and no filing line — neither a `#N` nor a reason.

### Root cause (the instance)

The claim was written at layer 4 (`devclaw/queue/settle.py`) about an action
owned by an edge that runs elsewhere, later, and conditionally: self-issue
filing (`devclaw/goal/self_issue.py`) fires once per run-cycle and only for a
problem `should_file` accepts — `cycle_count >= DEVCLAW_SELF_ISSUE_MIN_CYCLES`
(default 2), i.e. the problem must survive **two distinct run-cycles**.

For an environment deficiency that bar is unreachable **by construction**: the
same settle that records the catalog row also places a project-wide
`mechanical:env` hold, so every goal on the project stops dispatching against
that environment. The failure therefore cannot recur, so its cross-cycle count
cannot reach 2, so it can never be filed. The hold and the filing threshold
deadlock each other. Nothing failed and nothing was logged — the operator read
a claim with no record behind it.

### The class

A message asserts an action the pipeline did not verifiably perform. Two rules
fall out, and this spec applies both at the one seam #818 names:

1. **The layer that performs the action states the outcome.** A layer that
   cannot perform an action must not promise it — it names the owner, not the
   result.
2. **A recurrence threshold is a noise filter for transient failures.** A
   deterministic, terminal-by-construction fact — the sandbox lacks a tool —
   is not noise, and gating it on recurrence is a guaranteed never-file.

## Clarifications

### Session 2026-09-07 (author-resolved, from the issue's own Done-when)

- **Q: file immediately, or fix the recurrence counting?** — File immediately,
  at the moment the hold is placed. Fixing the count (e.g. crediting a held
  goal with a phantom recurrence) would file the gap a day late and make the
  cross-cycle count mean two different things. The gap is known, terminal and
  actionable the instant it is reported.
- **Q: a new issue writer, or the existing doorway?** — The existing doorway
  (`devclaw/issue_doorway.py`). It is the ONE machine-issue writer (spec 014),
  it dedups on the ledger by fingerprint, and it already records a
  problems-catalog row on a failed filing. A second writer would be the #630
  class of smell.
- **Q: what does the text say when `DEVCLAW_SELF_REPO` is unset?** — It says
  that, by name. An unset self-repo is a stated instance-configuration gate,
  not a failure: no catalog row, but never silence either.
- **Q: does the filing block the settle?** — No. The filing outcome is
  recorded; a filing failure never changes the hold, which is the fact that
  protects the project's sessions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The hold says what actually happened to the gap (Priority: P1)

A worker reports `BLOCKED: env — <item>`. The project holds on
`mechanical:env` exactly as today. In the same settle, devclaw files the gap
as devclaw work through the issue doorway and records the real outcome in the
goal log, the hold text and the owner ping: `filed as devclaw work: #N <url>`,
or `already tracked as devclaw work: #N`, or `NOT filed: <the rule or error
that stopped it>`. The operator can follow the claim to a record, or read why
there is none.

**Why this priority**: this is #818's whole Done-when. Without it the pipeline
lies about its own bookkeeping, which is the one thing an unattended loop's
operator has to be able to trust.

**Independent Test**: settle a task whose result is `BLOCKED: env — dotnet-ef
not available` with a fake `gh` and `DEVCLAW_SELF_REPO` set; assert the goal
holds on `mechanical:env`, exactly one issue was created, and the goal log
carries the issue number. Repeat with the fake failing and with the self-repo
unset; assert the log names the reason both times and the hold is unchanged.

**Acceptance Scenarios**:

1. **Given** `DEVCLAW_SELF_REPO` is set and the gap has never been filed,
   **When** the deficiency settles, **Then** one doorway issue is created, the
   goal log and the hold text name `#N` with its URL, the catalog row is linked
   to `#N`, and `FakeClaude.calls == 0`.
2. **Given** the same gap is reported again (same or another project), **Then**
   no second issue is created — the doorway appends an occurrence to `#N` and
   the text says `already tracked`.
3. **Given** the doorway fails (gh unavailable / creation rejected), **Then**
   the goal still holds on `mechanical:env`, the text names the failure, and
   the problems catalog carries the doorway's `issue_filing_failed` row.
4. **Given** `DEVCLAW_SELF_REPO` is unset, **Then** no subprocess is spawned,
   the text says the gap was not filed because the self-repo is unconfigured,
   and the hold is unchanged.
5. **Given** any of the above, **Then** the task-layer settle text no longer
   asserts a filing it does not perform.

---

### User Story 2 - A mis-configured instance is visible before it swallows a gap (Priority: P2)

`DEVCLAW_SELF_REPO` unset turns the whole self-improvement path into a no-op.
Today that is documented and otherwise invisible. Doctor gains an instance
check that reports the unset self-repo as a finding when the problems catalog
holds at least one worker-reported environment deficiency — the configuration
gap is named on the instance that is actually losing filings, not on every
dev checkout.

**Why this priority**: US1 makes the silence audible in the goal log; this
makes it audible on the instance dashboard. Useful, not load-bearing.

**Independent Test**: seed a catalog with a `block/env_deficiency` row and an
unset self-repo; assert one finding naming `DEVCLAW_SELF_REPO`. Remove the row
or set the env var; assert no finding.

---

## Requirements *(mandatory)*

### Functional Requirements (US1)

- **FR-001**: When a worker-reported environment deficiency places a
  `mechanical:env` hold, devclaw MUST attempt to file the gap as devclaw work
  in the same settle, through `devclaw/issue_doorway.py` — never through a
  second issue-writing path.
- **FR-002**: The finding's fingerprint MUST be the problems-catalog
  fingerprint of the deficiency row (`block | env_deficiency | <item>`), so the
  doorway ledger, the catalog and the cycle-close filer share ONE identity and
  a repeat can never open a second issue.
- **FR-003**: A successful filing MUST link the catalog row to the issue
  (`issue_number`/`issue_state`), so the once-per-cycle filer sees it as already
  tracked and the console's problem lifecycle renders *filed*.
- **FR-004**: The goal log, the `blocked_on` text and the owner ping MUST carry
  the filing outcome: the issue number and URL when filed or already tracked,
  or the reason it was not filed.
- **FR-005**: Filing MUST NOT be able to change the hold. Any failure — a gh
  error, a doorway exception, an unset self-repo — leaves the `mechanical:env`
  block, its ping marker and its heal exactly as they are today.
- **FR-006**: With `DEVCLAW_SELF_REPO` unset the filing path MUST spawn no
  subprocess (the default for every dev checkout and every test).
- **FR-007**: A filing failure MUST leave a problems-catalog row (the doorway's
  `delivery/issue_filing_failed`), never silence. This covers EVERY failed
  attempt, not only the ones the doorway sees: a caller's wall-clock bound
  (FR-010) cancels `file_finding` mid-call and anything raised before it is
  entered never reaches its handler, so the caller records those itself through
  the doorway's own recorder — one kind for the whole class, however it failed.
  An unset self-repo (FR-006) is not a failed attempt and records nothing.
- **FR-008**: `devclaw/queue/settle.py`'s environment-deficiency text MUST stop
  asserting that the gap is filed; it names devclaw as the owner and leaves the
  outcome to the layer that performs it.
- **FR-009**: The goal layer MUST reach the ledger through thin `GoalStore`
  passthroughs, never by touching `GoalStore._state` (the existing
  `record_problem` / `get_meta` shape).
- **FR-010**: The filing MUST be bounded by wall clock. It runs inside the
  heartbeat sweep, so a `gh` that never answers must degrade to a stated
  non-filing, never hang the fleet's tick (precedent:
  `devclaw/goal/remote_checks._gh`).
- **FR-011**: An environment deficiency's issue MUST be exempt from the
  once-per-cycle **age-out** close, for the same reason it is exempt from the
  recurrence bar: the hold makes it quiet by construction, so aging it out
  would close devclaw's own unfixed issue while the project is still red — and
  leave the hold text pointing at a closed issue.

### Key Entities

- **Environment deficiency** — `(item, project)`; already a red `worker:*`
  capability row (`devclaw/env_cap.py`) and a `block/env_deficiency` catalog row.
- **Machine issue ledger** — `machine_issues(repo, fingerprint)`; the doorway's
  dedup source of truth.

## Success Criteria *(mandatory)*

- **SC-001**: Zero settle/hold messages claim a filing without a `#N` or a
  stated reason beside it.
- **SC-002**: A worker-reported environment gap on a configured instance
  produces exactly one GitHub issue, within one heartbeat of the settle — not
  one run-cycle, not never.
- **SC-003**: No LLM call is added to the settle path (`FakeClaude.calls == 0`
  across every new case).

## Known gap (not this spec's)

The cycle-close age-out writes `problems.issue_state = 'closed'` but leaves the
doorway's `machine_issues` ledger row `'open'`, so a later recurrence of ANY
aged-out class comments on a closed issue and reports it as tracked. FR-011
takes environment deficiencies out of that path entirely; the general drift is
pre-existing, affects the other classes, and belongs to its own change.

## Out of scope

- Changing the recurrence threshold or the cycle-close filer for ordinary
  problems. The threshold is right for noisy, transient failures; this spec
  only removes from behind it the one class that cannot satisfy it (FR-002/FR-011).
- Auto-fixing the environment. The gap is filed for a human/self-fix pickup;
  the hold is released on its own terms, not by this spec. (Those terms changed
  under us while this spec was in flight: `specs/tiny/env-hold-observes-the-capability`
  made a worker-reported row human-gated — `resume_goal`, not an `env_ref`
  change. See T011.)
- Any change to the `mechanical:env` hold, ping or heal semantics.

## Rejected alternatives

- **Credit a held goal with a synthetic recurrence so the 2-cycle bar is met.**
  Files a day late and corrupts the meaning of the cross-cycle count for every
  other problem class.
- **Drop `RECURRENCE_THRESHOLD` to 1.** Fixes this class by breaking the noise
  filter for all the others; the 2026-07-28 data (93 problems / 7 cycles, max
  survival 2) is why the filter exists.
- **File from `devclaw/queue/settle.py`, where the claim was written.** Layer 4
  has no business opening GitHub issues and no project/goal context; the
  layer-2 hold seam already knows the project, the goal and the capability id.
- **Make the settle text conditional on a later filing.** The settle cannot
  know; the fix is for it to stop claiming, not to guess better.
