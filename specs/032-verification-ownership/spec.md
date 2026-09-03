# Feature Specification: Verification ownership

**Feature Branch**: `032-verification-ownership`

**Created**: 2026-09-03

**Status**: Implemented 2026-09-03 — US1 (#811), US2 (#812), US3 (#813), US5 + doctrine amendment + US4 declaration surface (#814). US4 provisioning-or-refusal is DEFERRED by ruling (Q1 = C) until US1 has a live track record; it is the remaining owned work of this spec, tracked in tasks.md Phase 7 / research R8.

**Input**: User description: "Verification ownership: the pipeline verifies a change in the
project's declared environment, never in devclaw's generic sandbox, and the worker never
edits a project's gate inputs to make a gate pass." (Full input in the session log,
2026-09-03; distilled below.)

## Why (the root, not the incident)

devclaw exists to run the SDLC without a human: every stage has a mechanical exit
criterion, cognition is used only where judgment is needed, and a human appears only on a
typed, bounded Problem with a default. The 2026-09-03 audit of 25 goals closed "achieved"
since 2026-08-19 found that 7 needed the owner's hands within days. Tracing those back
through the code (not the symptoms) lands on two design decisions:

1. **Two environments, one declared truth.** The verify gate runs *inside* the generic
   sandbox (ADR 0005: base image + the SDK mise can install; no services, no project
   tools, no project secrets, host networking) and its exit code is the verdict of
   record. The project's own verification environment — its CI, with a database
   service, static analysis and an image build — is consulted by nothing mechanical; the
   done-gate sees it only as prose in a review. Consequences on record: a hand-written EF
   migration without its discovery attributes passed every gate and broke finance-sentry
   production on 2026-09-02 (`alerts.alerts` queries failing every minute); four goals
   (fs-421, fs-431, issue-443, fs-318) burned done-gate rounds on a red CI the loop could
   not read; integration tests are permanently excluded from the gate because the sandbox
   cannot host a database, so the whole migration/SQL class is invisible to every gate.
2. **The worker's only writable surface is the product repo, and its skill licenses
   editing the gate.** `runner/skills/_writes-code/50-repo-gate-conflict.md` says "fix
   the mechanism, don't obey it", permits `--no-verify`, and asks the worker to document
   the bypass in AGENTS.md. It was written for a repo mechanism (a hook) conflicting with
   a ticket. Applied to the sandbox conflicting with the repo, it produces exactly what
   the audit found: a committed arm64 `.so`, `LD_LIBRARY_PATH` in a Playwright config,
   a 351-line postinstall monkey-patch of `node_modules` that prints "patched" even when
   it patched nothing, an `angular.json` browser swap, four devclaw goals each deleting a
   true CLAUDE.md line to pass a test under tmpfs, and "`--no-verify` is required per repo
   convention" written into a product PR body as if it were the repo's rule.

The smell, named once: **state of the pipeline's environment persisted in the product's
repository, and a verdict of record produced somewhere the code will never run.**

Instance fixes already stacked on this root (evidence for the class; each is retired or
subsumed by this spec): Playwright system libraries baked into the sandbox image after
the `LD_LIBRARY_PATH` hack (`.sandcastle/Dockerfile`); spec 030's capability probes for
one token and one image; the doctrine line "post-merge human review is the backstop"
(CLAUDE.md, docs/architecture.md, constitution Principle V); `Category!=Integration` in
finance-sentry's gate.

**Principle this spec is checked against**: a stage's exit criterion is a fact the
pipeline owns. Where a fact exists, no LLM reads prose for it and no human is asked for
it. Where a fact cannot exist in devclaw's environment, the pipeline obtains it from the
environment that has it, or refuses to proceed — it never lets the worker improvise.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The project's CI verdict is a fact the loop requires (Priority: P1)

A goal's worker finishes an increment; delivery pushes the cumulative PR; the project's
CI runs against the pushed head. Before the done-gate spends any cognition, the loop
reads the PR's check rollup for that exact head. Red ⇒ no done-gate round is consumed;
the goal advances with the failing check named as its next correction. Pending ⇒ the
loop waits (bounded), it does not judge. Green ⇒ the done-gate runs as today. At the
close, merge-on-close requires the same green fact for the head it merges.

**Why this priority**: it is the cheapest fact with the largest effect — it ends the
red-CI churn, makes the migration class impossible to merge, and needs no environment
provisioning at all.

**Independent Test**: seed a goal with a delivered PR whose rollup is red; tick; assert
zero evaluator calls, a correction naming the check, and no merge attempt. Flip the
rollup green; assert the done-gate runs and merge proceeds.

**Acceptance Scenarios**:

1. **Given** a delivered head with a red required check, **When** the goal proposes done,
   **Then** the done-gate is not invoked (`FakeClaude.calls == 0`), the goal's next action
   names the failing check by name, and no owner is pinged.
2. **Given** a green rollup for the judged head, **When** the done-gate returns achieved,
   **Then** merge-on-close proceeds; **Given** the head changed after the green read,
   **Then** the merge is refused until the new head's rollup is green.
3. **Given** a rollup pending for longer than the wait bound, **Then** the goal records a
   `mechanical:ci` hold naming the pending check and re-reads on the next tick; no
   cognition is spent while held.

---

### User Story 2 - The worker has a legitimate outlet for an environment gap (Priority: P1)

Inside a session the worker hits something its environment cannot do: a tool the
project's verify needs is absent, a service is unreachable, a registry rejects the
token. It stops and reports `BLOCKED: env — <what is missing>` in the typed result. The
pipeline, not the worker, owns what happens next: the project is held on
`mechanical:env`, the deficiency is recorded once in the problems catalog and filed as
devclaw work through the existing self-improvement path, and the owner is informed once.
The worker never patches the product to route around its own environment.

**Why this priority**: this is the outlet whose absence caused every leak in the audit.
Without it, root 1's fix just moves the improvisation from AGENTS.md into the code.

**Independent Test**: run the fake agent with a scripted `BLOCKED: env` result; assert
the task settles as an environment deficiency (not a failure to retry), the project holds,
one catalog row exists with the capability named, and no product file was changed.

**Acceptance Scenarios**:

1. **Given** a session whose result line is `BLOCKED: env — dotnet-ef not available`,
   **When** it settles, **Then** the goal holds on `mechanical:env` with that text, no
   retry is scheduled, and the problems catalog gains one row keyed by the capability.
2. **Given** the same deficiency reported by a second project, **Then** the catalog row
   count is 1 (deduplicated by capability), and both projects reference it.
3. **Given** the deficiency clears (the declared environment now provides the tool),
   **Then** both projects resume without any human verb (spec 030 heal shape).

---

### User Story 3 - Gate-input edits are classified and never count as evidence (Priority: P2)

At delivery, every changed path is classified: **product**, **gate-input** (AGENTS.md,
CI workflow files, test-runner and build configs such as Playwright/Angular/pytest
configs, install and postinstall scripts, lockfile toolchain pins, `.devcontainer`,
committed binaries), or **environment declaration** (the project manifest's environment
section). A gate-input edit fails the task in both strictness modes, with the path named
and no retry; the worker's legitimate moves are `BLOCKED: env` (US2) or a contract-level
Problem, never the edit. No gate-input file is ever evidence for a done_when clause. An
issue may declare a gate-input path or category as in scope (a ticket that *is* about
CI), which classifies those paths as product for that goal only.

**Why this priority**: it is the structural guard that makes root 2 impossible to
reintroduce by a future skill edit; it is independent of US1/US2 and ships alone.

**Independent Test**: materialize a span touching `AGENTS.md` and a `.so`; assert the
task fails in both modes naming both paths, no retry is scheduled, and the done-gate's
evidence input never sees them.

**Acceptance Scenarios**:

1. **Given** a span that edits `playwright.config.ts` and `src/app/x.ts`, **When** it
   settles under `trust`, **Then** the task fails with the config path named, no retry
   is scheduled, and nothing from the span is delivered.
2. **Given** the same span under `strict`, **Then** the outcome is identical — the rule
   does not ride the strictness dial.
3. **Given** the goal's issue declares "CI workflow" in scope, **Then** the workflow
   edit classifies as product and no advisory is raised.

---

### User Story 4 - The verification environment is the project's, provisioned or refused (Priority: P2)

A project declares its verification environment in its manifest: the dev image or
devcontainer, the services its verify needs, the tools beyond the SDK, the registries it
reads. Dispatch admission checks the whole declaration is satisfiable (spec 030's
capability check generalized from two ids to the declaration); the runner provisions
exactly what is declared before the agent starts; anything not declared is not present,
and anything declared but unavailable holds the project on `mechanical:env` naming the
item. The in-sandbox `verify_cmd` remains a fast pre-check whose pass is never evidence
and whose fail still fails the task.

**Why this priority**: it is the completion of ADR 0005's "the toolchain is the
project's fact" to "the *environment* is the project's fact", and it is what lets
integration-class checks run before CI rather than only in CI. Ruled 2026-09-03 (Q1):
it lands AFTER US1–US3 have a track record on the live instance; until then the sandbox
stays generic (ADR 0005) and the CI rollup is the sole verdict of record.

**Independent Test**: register a project declaring a service the instance cannot
provide; tick; assert no dispatch, a hold naming the service, zero LLM calls. Declare
only what the instance provides; assert dispatch proceeds with the declared items
present in the sandbox and nothing else.

**Acceptance Scenarios**:

1. **Given** a manifest declaring a tool the runner cannot provision, **When** a goal on
   that project comes up, **Then** the project holds with the tool named, one ping, zero
   sessions.
2. **Given** a manifest with no environment declaration, **Then** admission behaves as
   today for that project and doctor raises an advisory (write-and-forget guard, spec 030
   FR-005a's shape).

---

### User Story 5 - The loop measures its own dependence on the human (Priority: P3)

The scorecard reports **human interventions per achieved goal**: every steer, resume,
decide, correct_implementation, and every commit on a goal branch not authored by the
worker, divided by goals closed achieved in the window. The audit window scores about
0.3; the number the loop is judged by is this one trending to zero while the achieved
count does not fall.

**Why this priority**: without it, "works without me" stays an opinion.

**Independent Test**: seed a window with two achieved goals, one steer and one
non-worker commit; assert the metric reads 1.0 and each term is itemized.

**Acceptance Scenarios**:

1. **Given** the seeded window, **When** the scorecard is read, **Then** the metric and
   its itemized terms are present and the goal ids are listed per term.

---

### Edge Cases

- **Project with no CI at all** (ruled 2026-09-03, Q3): a project without a CI
  definition has no verification environment and is not dispatchable; it holds on
  `mechanical:env` naming the gap until onboarding produces one. Onboarding already
  writes `.devcontainer/Dockerfile` and gains the CI definition as a sibling artifact.
- **Flaky CI**: a check that fails and passes on re-run is a project fact, not an
  environment gap. The loop re-runs failed checks once, mechanically; a second failure
  is a correction for the worker ("fix or quarantine the flaky test"), never a bypass.
- **Which checks count**: the repository's required checks when branch protection
  defines them; otherwise every check on the head. Advisory-class scanners (CodeQL
  alerts) count when the repository marks them required and not otherwise; the
  classification is read from the repository, never configured in devclaw.
- **CI provider unreachable**: the rollup read is `unknown`; unknown never holds and
  never approves — the done proposal waits one bounded period and re-reads (spec 030
  FR-007's fail-open-on-uncertainty, fail-closed-on-evidence).
- **The ask is a gate-input change**: the issue names the path or category in scope;
  classification honors it for that goal only.
- **Environment lore already in repos**: cleaning existing AGENTS.md sandbox sections,
  committed binaries and install shims out of finance-sentry, lifekit-dashboard and
  lifekit-common is product work filed per repo, out of this spec's scope; the
  classifier stops new ones.
- **Repo mechanism vs ticket** (the case the old skill was written for): a hook that
  forces a forbidden edit is a contract-level Problem for the goal (spec 031), raised with
  options "relax the hook in this repo" / "amend the ticket", never a bypass.
- **Mid-session environment break** (registry token expires during a run): the worker
  reports `BLOCKED: env`; the pipeline treats it exactly as US2.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pipeline MUST read the cumulative PR's check rollup for the exact
  delivered head as a mechanical fact (green / red / pending / unknown) before any
  done-gate evaluation and before any merge attempt. Red ⇒ no evaluator call, a
  correction naming the failing check. Pending ⇒ a bounded wait on a `mechanical:ci`
  hold. Unknown ⇒ wait, never approve, never hold as broken.
- **FR-002**: Merge-on-close MUST require a green rollup for the head it merges; a head
  that changed since the last green read MUST be re-read.
- **FR-003**: The in-sandbox `verify_cmd` MUST remain a fail-closed pre-check: its
  failure fails the task as today; its pass MUST NOT be presented to the done-gate as
  evidence for any clause.
- **FR-004**: The worker's typed result MUST distinguish an environment deficiency
  (`BLOCKED: env — <item>`) from a failure to finish. An environment deficiency MUST NOT
  be retried, MUST hold the project on `mechanical:env` with the item named, MUST be
  recorded once in the problems catalog keyed by the item, and MUST be filed as devclaw
  work through the existing self-improvement path. The owner is informed once per item.
- **FR-005**: The worker skill set MUST NOT license bypassing a gate (`--no-verify`,
  `SKIP=`), weakening a check, or recording environment workarounds in AGENTS.md. A repo
  mechanism that conflicts with the ticket MUST become a contract-level Problem.
- **FR-006**: Delivery MUST classify every path in the materialized span as product,
  gate-input, or environment declaration, using one classifier that every consumer
  reads (the one-definition-of-change rule). A gate-input edit or a committed binary
  MUST fail the task in both strictness modes, naming the paths, with no retry.
- **FR-007**: The evidence input to the done-gate MUST exclude gate-input paths, and the
  evaluator prompt MUST state that AGENTS.md, CI configuration, test-runner configuration
  and install scripts are never evidence for a clause.
- **FR-008**: A goal's issue MAY declare a gate-input path or category as in scope; the
  classifier MUST honor that declaration for that goal only.
- **FR-009**: The project manifest MUST be able to declare the verification environment
  (image or devcontainer, services, tools, registries). Dispatch admission MUST hold the
  project when any declared item is unsatisfiable (spec 030's mechanism, generalized),
  and the runner MUST provision exactly the declared items before the agent starts.
- **FR-010**: A project that declares no environment MUST behave as today for admission,
  with a doctor advisory naming what its repository visibly depends on.
- **FR-011**: Rollup reads and admission checks MUST be off the zero-token idle path:
  they run on the settle / done-proposal / merge path and on the per-sweep probe cadence
  only, never on an idle tick, and never call an LLM.
- **FR-012**: The scorecard MUST expose human interventions per achieved goal over a
  window, itemized by term and goal id, computed from the store only.
- **FR-013**: The constitution's Principle V MUST be amended in the same arc: "the human
  reviews merged work post-merge, revert is the remedy" is replaced by "the project's own
  verification environment is the verdict of record and the validation lane is the
  backstop; the human is not a stage". CLAUDE.md and docs/architecture.md drop the
  "post-merge human review is the backstop" lines in the same PR.
- **FR-014**: The instance fixes this spec subsumes MUST be retired in the same arc: the
  image-baked Playwright rationale comment is rewritten as a declared-environment note;
  spec 030's status line records that this spec generalizes its capability set.

### Key Entities

- **Check rollup fact**: for a (PR, head) pair: green / red / pending / unknown, the
  names of failing or pending checks, and the read time. Owned by the pipeline, never
  cached across heads.
- **Environment declaration**: the manifest section naming image/devcontainer, services,
  tools, registries. The project's fact; admission and provisioning read it verbatim.
- **Environment deficiency**: an item the declared environment needs and the instance
  cannot provide, reported by the worker or found at admission; keyed by item; one
  catalog row; holds every dependent project.
- **Change class**: product / gate-input / environment declaration, assigned per path in
  the materialized span by one classifier; read by delivery, the gates and the done-gate.
- **Intervention**: a human verb or a non-worker commit on a goal branch, attributed to a
  goal; the numerator of the north-star metric.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A change whose project CI is red cannot merge and consumes zero done-gate
  rounds while red; in the seeded fixture, `FakeClaude.calls == 0` across a full sweep.
- **SC-002**: The fs-432 class is unreproducible: a migration that never applies fails the
  project's CI, and that failure reaches the goal as a named correction within one tick
  of the rollup turning red, with no merge attempted.
- **SC-003**: Zero undeclared gate-input edits and zero committed binaries reach a
  product repository across every goal in the window.
- **SC-004**: Zero worker sessions are spent improvising around an environment gap: every
  such gap ends the session as `BLOCKED: env` within that session and appears once in
  the problems catalog.
- **SC-005**: Human interventions per achieved goal is reported on the scorecard for any
  window and, over the four weeks after the arc lands, trends down from the audit
  baseline (about 0.3) without the achieved count falling.
- **SC-006**: A project that declares nothing behaves byte-identically to today at
  admission (spec 030 SC-003 preserved).

## Assumptions

- Every registered project's CI is GitHub Actions on the same repository as its PRs, and
  the host already holds read access to check results (delivery already reads PR state
  through the same access).
- "Required checks" come from the repository's branch protection when defined; when not
  defined, every check counts. devclaw never maintains its own list of which checks
  matter.
- The bounded wait for a pending rollup is on the order of the project's typical CI
  duration and rides the existing heartbeat cadence; it is a knob with a default, not a
  new mechanism.
- An environment deficiency is devclaw's work, not the project's: the fix is a change to
  the runner, the image or the declaration, filed on the devclaw repository through the
  problems-catalog self-issue path that already exists.
- Existing environment lore in product repositories is cleaned up as ordinary product
  issues per repository; this spec prevents new lore and does not rewrite history.
- The sibling spec on evidence provenance and done (roots 3–5 of the audit) consumes the
  change classes and the rollup fact defined here; it does not redefine them.

## Rejected alternatives (recorded per the direction-memory rule)

- **Teach the worker to be careful** (skill prose telling it not to commit workarounds):
  rejected — spec 030 already recorded that prose cannot make a deterministic
  environment failure non-deterministic; the audit shows six different improvisations
  around the same wall. Structure, not instruction.
- **Keep the sandbox as the verdict of record and enlarge it** (bake services and tools
  into devclaw's image): rejected — ADR 0005 rejected per-stack images for ownership and
  reproducibility reasons that apply doubly to services; devclaw would own every
  project's environment forever.
- **Ask the human on every gate-input edit** (a Problem per edit): rejected — it adds a
  human stage where a mechanical classification exists.
- **Ship gate-input edits as an advisory under `trust`** (the browser gate's ADR 0007
  shape): rejected 2026-09-03 (Q2) — an advisory is a post-merge human stage by another
  name; the worker has two legitimate moves (`BLOCKED: env`, a contract Problem) and a
  ticket that is about CI declares its paths in scope.
- **Patch the instances** (a Designer-file check here, a `.so` check there, a Playwright
  config lint): rejected — each is one more keyword against one more symptom; the
  classifier and the outlet close the class. The Designer-attribute test shipped in
  finance-sentry PR #555 is a bleeding-stop and is labeled as such there.
- **Make CI the only verification and drop in-sandbox verify entirely**: rejected —
  the fast pre-check catches the cheap failures before a push and keeps the session's
  own feedback loop short; only its *authority* is removed.

## Clarifications (2026-09-03, walked with Denys one at a time)

- **Q1 — Where the verification environment comes from**: C — the CI rollup is the
  sole verdict of record now (US1–US3 ship first); the project-declared environment
  with services (US4) lands as P2 once that has a track record. Rejected: A (drop US4
  for good — leaves the integration class CI-only forever) and B (provision services
  in-sandbox in this arc — the costliest engine work before the cheap fact has proven
  itself).
- **Q2 — Gate-input edits under `trust`**: B — fail the task in both modes; the worker
  reports `BLOCKED: env` or raises a contract Problem; an issue that is about CI
  declares its paths in scope. Rejected: A (advisory — a human post-merge stage by
  another name) and C (a Problem per edit — a human stage where a classification
  exists).
- **Q3 — A project with no CI definition**: A — not dispatchable until onboarding
  writes one; held on `mechanical:env` naming the gap. Rejected: B (sandbox verify as
  the verdict for that project — keeps the two-environments smell alive) and C
  (dispatchable but never closes — a human stage at every close).
