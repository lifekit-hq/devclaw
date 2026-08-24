# Feature Specification: Live-Validation Loop

**Feature Branch**: `015-live-validation-loop`

**Created**: 2026-08-24

**Status**: Implemented (2026-08-24)

**Input**: User description: "Every spec ships executable acceptance tests at its outermost surface; a validate_product mechanism boots a hermetic seeded instance from a repo-declared contract, runs the accumulated suites, and files findings as issues through the spec-014 doorway — never blocking, never opening PRs. Companion-first triggering. Grilled and locked with Denys 2026-08-24."

## Why (direction memory)

devclaw's entire verification stack proves properties of the *tree*, never of the
*running process*: `verify_cmd` is build+unit truth in the sandbox, the diff review
is static, the done-gate reads code. The browser gate is the one existing
exception, built after a component passed every gate while throwing the moment its
dropdown opened. The trigger for this spec was the same scar on the backend side:
finance-sentry's liquidity sentinel registers a scheduled job via reflection-based
module discovery, and no test anywhere could prove the running system actually
schedules it. Grilled end-to-end with Denys on 2026-08-24 (15 decisions; owner
vault log 2026-08-24): this spec generalizes runtime verification into a loop that
validates the *product* and feeds what it finds back into devclaw as work.

**Rejected alternatives** (recorded per the direction-memory rule):

- *A blocking pre-merge runtime gate as the spine* — rejected: re-adds the wedge
  class spec 001 removed; the spine must not be able to wedge deliveries. (A
  later, dial-riding ephemeral-boot gate remains open as a possible follow-up
  once the loop has a track record — explicitly not in this spec.)
- *A maintained prose acceptance catalog* — rejected: a second writer that drifts.
  The accumulated executable suite IS the catalog. (An exploratory UX-judgment
  layer for what cannot be automated is acknowledged and deferred.)
- *Nightly validation cadence* — rejected: validating an unchanged deploy is spend
  without information; quota is the scarce resource.
- *Running e2e against production* — rejected: e2e creates and mutates data; prod
  (real financial data) gets read-only smoke only.
- *Adopting `kunchenguid/no-mistakes`* — rejected (see spec 014's record).
- *Re-enabling automerge now* — rejected: human-merge stands (#641). Automerge may
  be earned back only by this loop's demonstrated track record, with the bot
  identity and branch protection as prerequisites; that is a future decision, not
  part of this spec.
- *Standing autonomous schedule at launch* — amended by Denys in-session:
  companion mode is primary, so continuation-autonomy is deferred. The loop ships
  with the post-deploy trigger only (human-initiated by construction — deploy is
  the owner's button-press); the periodic schedule exists but ships OFF.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every spec ships executable acceptance tests (Priority: P1)

From this feature on, a spec's acceptance scenarios are executable: each spec
carries acceptance tests at its *outermost surface* — browser-driven end-to-end
tests for UI flows, HTTP-level tests against the running service for backend
behavior, observation of the running scheduler for background jobs. The
requirement is enforced where it can be enforced: upstream, the spec template and
intake grading require scenarios to be expressible as executable acceptance tests
before work is `devclaw-ready`; downstream, the existing browser gate demands an
*executed* run (positive executed count, never a grep for intent) for increments
whose spec carries UI-facing scenarios, and the done-gate names acceptance
scenarios that have no covering test — the check that catches a worker writing
tests that confirm what was built rather than what was specced.

The accumulated suite is the product's acceptance catalog. There is no prose
catalog to maintain.

**Why this priority**: Without executable ground truth there is nothing for the
validator to run; every other story consumes this one's output.

**Independent Test**: Grade an issue whose acceptance criteria cannot be expressed
as executable tests — it must not become ready; ship an increment for a UI-facing
spec scenario without an executed e2e run — the gate must report it.

**Acceptance Scenarios**:

1. **Given** an intake issue whose acceptance criteria are e2e-expressible,
   **When** it is graded, **Then** it can earn readiness; **Given** one whose
   criteria cannot be validated by any executable test, **Then** grading returns
   it for refinement with that reason.
2. **Given** an increment implements a spec scenario at a UI surface, **When** it
   settles without evidence of an executed browser run covering it, **Then** the
   gate reports it (advisory under `trust`, closed under `strict` — the existing
   dial, unchanged).
3. **Given** a goal proposes done while a spec acceptance scenario has no covering
   test, **When** the done-gate evaluates, **Then** the uncovered scenario is
   named in its structural findings (riding the existing structural axis of the
   strictness dial).
4. **Given** a backend-only spec (no UI), **Then** the same rule binds at its
   outermost surface: tests exercise the running service or observe the running
   scheduler — unit tests alone do not satisfy it.

---

### User Story 2 - A validation run proves the running product (Priority: P2)

A new kind of task — `validate_product` — runs in the same sandboxed worker
harness as all other work, but its job is: boot the product as a hermetic,
seeded instance according to a contract the target repo itself declares (how to
boot each surface, how to seed it, how to run its suites); execute the accumulated
acceptance suites against that running instance; and emit every failure as a
finding through the spec-014 doorway. A validation run never blocks a delivery,
never opens a PR, and never mutates the product repo. Production is touched only
by a separate read-only smoke check after a deploy.

**Why this priority**: This is the mechanism that closes the class the spec was
born from — runtime wiring that no static check can prove.

**Independent Test**: In the stub environment, a `validate_product` task with a
seeded failing scenario produces exactly one schema-conformant finding and no PR,
no commit, no gate verdict.

**Acceptance Scenarios**:

1. **Given** a repo declaring a boot contract, **When** a validation run executes,
   **Then** the product boots hermetically (seeded, isolated, reproducible) and
   the accumulated suites run against the live instance.
2. **Given** a suite failure, **Then** a finding is filed through the spec-014
   doorway carrying the failing scenario reference as its expected-vs-actual and
   the run's evidence; repeated failures deduplicate by fingerprint (014 US2).
3. **Given** a fully green run, **Then** no issue is filed and the run's outcome
   is visible to the owner (a run record, not silence).
4. **Given** a repo that declares no boot contract, **Then** the validation run
   fails loud with an actionable reason — it never silently passes (fail-loud
   invariant), and the missing contract is itself filed through the doorway.
5. **Given** any validation run, **Then** it makes no LLM call during suite
   execution and never pushes, merges, or opens PRs.

---

### User Story 3 - Companion-first triggering (Priority: P3)

Validation runs are attached to a per-repo QA goal that continues indefinitely
(it has no terminal "done"), but its autonomy is deliberately restrained to match
companion mode: at launch, the only trigger is a completed deploy — which the
owner initiates by hand, so every validation run is human-caused by construction.
A periodic full-run schedule exists as configuration but ships OFF; arming it is
the owner's explicit act, taken when the loop has earned it. Prod receives only
the read-only post-deploy smoke; the e2e suites run only against the hermetic
instance.

**Why this priority**: The loop is useful with manual/post-deploy triggering
alone; the standing schedule is a dial on an already-working mechanism.

**Independent Test**: Complete a deploy in the stub environment — a validation
run is enqueued; advance time with the schedule OFF — no run is enqueued and no
cognition is spent (the zero-token idle guard tests stay green).

**Acceptance Scenarios**:

1. **Given** a deploy completes for a repo with a QA goal, **Then** one validation
   run is triggered for that repo.
2. **Given** the periodic schedule is OFF (the shipped default), **Then** no
   validation run is ever self-initiated, and idle ticks remain zero-cognition.
3. **Given** the owner arms the schedule for a repo, **Then** full runs occur at
   the configured cadence inside the run window, and disarming stops them.
4. **Given** existing shipped features with no acceptance coverage, **Then** the
   rule is forward-only — coverage accumulates from new specs; backfill happens
   through ordinary graded backlog issues per repo (filed as part of rollout),
   never by blocking the mechanism on retroactive coverage.

---

### Edge Cases

- The product fails to boot at all: that is itself a finding (the most severe
  kind), filed through the doorway with the boot evidence — not a crashed task
  loop.
- A flaky scenario alternating green/red across runs: fingerprint dedup (014)
  keeps it one issue accumulating occurrences; flake-vs-regression judgment stays
  human.
- Suites take longer than a sandbox run allows: the run reports partial coverage
  explicitly (which suites ran, which were cut) — silent truncation is forbidden.
- A repo with a boot contract but zero accumulated acceptance tests yet: the run
  is green-by-vacuity and says so explicitly in its run record.
- Deploy of a repo with no QA goal: nothing triggers — the loop is opt-in per
  repo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The spec template requires acceptance scenarios to be expressible as
  executable tests at the feature's outermost surface, and intake grading holds
  work back until they are.
- **FR-002**: The browser gate requires proof of an *executed* browser suite for
  increments implementing UI-facing spec scenarios (existing proof-of-execution
  semantics; consulted per the existing strictness dial).
- **FR-003**: The done-gate names spec acceptance scenarios with no covering test
  in its structural findings.
- **FR-004**: A repo can declare a validation contract: how to boot each surface
  hermetically with seed data, and how to run its accumulated acceptance suites.
- **FR-005**: A `validate_product` run boots the declared contract in the sandbox,
  runs the suites, and files each failure through the spec-014 doorway; it never
  blocks tasks, never creates commits/PRs, and makes no LLM call during suite
  execution.
- **FR-006**: An undeterminable validation outcome (missing contract, boot
  failure, crashed suite) is loud and itself filed — never a silent pass.
- **FR-007**: Validation runs attach to a per-repo QA goal with no terminal
  completion; its idle ticks cost zero cognition.
- **FR-008**: Triggers: post-deploy (on at launch) and periodic schedule (ships
  OFF, owner-armed, runs inside the run window).
- **FR-009**: Production receives only read-only smoke checks post-deploy; e2e
  suites never execute against production.
- **FR-010**: The acceptance-coverage rule is forward-only; backfill for existing
  features is filed as ordinary graded backlog issues per repo.
- **FR-011**: Every behavior above is covered by a named regression test; the
  zero-token idle-guard tests remain green.

### Key Entities

- **Validation contract**: the repo-declared description of how to boot, seed and
  test each of its surfaces.
- **Validation run**: one execution — boots, runs suites, emits findings and a run
  record.
- **Finding**: a suite failure rendered as a spec-014 machine finding.
- **QA goal**: the per-repo, non-terminating owner of validation runs and their
  triggers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A runtime-wiring defect of the class that motivated this spec (a
  scheduled job that never registers in the running product) is caught by a
  validation run and arrives as a schema-conformant issue with zero human steps.
- **SC-002**: 100% of validation findings parse against the spec-014 schema and
  deduplicate across repeated runs.
- **SC-003**: With the schedule OFF, cognition spend attributable to the QA goal
  on idle days is zero.
- **SC-004**: A deploy of an opted-in repo yields exactly one validation run and
  one read-only prod smoke, visible in the owner's status surfaces.
- **SC-005**: An issue whose acceptance criteria are not e2e-expressible cannot
  reach readiness without refinement.

## Assumptions

- Spec 014 (the doorway) ships first; this spec files exclusively through it.
- The sandbox image already carries a browser and the runner already parses
  machine-readable suite reports (existing browser-gate plumbing) — reused, not
  rebuilt.
- Human-merge remains the delivery contract throughout (#641); nothing in this
  spec merges anything.
- The constitution requires no amendment: the validator is not a gate in the task
  chain (it emits intake, not verdicts), consulted gates keep their fail-closed
  semantics, and the zero-token idle guard is preserved by FR-007/FR-008.

## Out of Scope

- Any blocking pre-merge runtime gate (possible future follow-up, separate spec).
- Automerge and its earn-back conditions (future decision; prerequisites noted in
  the direction-memory section).
- The exploratory UX-judgment layer for non-automatable qualities.
- The devclaw-bot identity (tracked as #399, M3).
- Cross-feature/product-level test generation by LLM at validation time.
