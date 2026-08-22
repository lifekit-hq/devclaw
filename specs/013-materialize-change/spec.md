# Feature Specification: One definition of the change

**Feature Branch**: `docs/spec-013-materialize-change`

**Created**: 2026-08-22

**Status**: Draft

**Input**: Issue [#630](https://github.com/lifekit-hq/devclaw/issues/630). Materialize the agent's change once, mechanically, as a git object, and have every consumer read that same object instead of recomputing its own view.

## Problem

Two parts of the system independently compute "what did the agent change?", and they disagree.

**Delivery computes it mechanically** — it stages everything in the workspace before committing, so it cannot miss a file.

**The gates compute it by trusting the agent** — they diff the workspace against the pre-run commit, which shows only content the agent chose to record in the repository. The instruction that makes the agent record its work lives in a worker skill file: *"COMMIT your change yourself … ONE commit, staging everything."* A load-bearing correctness invariant, enforced by a request to a language model.

When the agent complies, both views agree and nothing looks wrong. That is why this survived until it was caught by accident. When it does not comply, the gates judge a strict subset of what ships.

Measured live on 2026-08-22 (task `78201bce`, repo `dsdevq/devclaw-shakedown-598`):

| | files | lines |
|---|---|---|
| what delivery shipped | 4 | +179 |
| what the gates judged | 1 | +32 |

Three new files were created and left unrecorded; only the one pre-existing modified file was judged. A change made entirely of new unrecorded files reaches the gates as an **empty** span and passes every one of them trivially.

This is a Principle IV problem wearing a different hat. The constitution already says only one component may own a piece of state; here two components own the *definition of the change*, and the property that is supposed to bind them ("the exact span the gates judged and delivery ships") exists only as a sentence in a docstring.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The gates judge everything that will ship (Priority: P1)

As the human who reviews every PR devclaw opens, when a gate reports that a change passed, I need that verdict to cover the whole change — not the part of it the agent happened to record.

**Why this priority**: This is the defect. Until the judged span is complete, every gate verdict is conditional on the agent's goodwill, and a clean report can mean "nothing was wrong" or "we looked at almost nothing". Those are indistinguishable today.

**Independent Test**: Run a task whose entire output is new files the agent does not record. Before: gates see an empty span and pass. After: gates see all of it. Delivering the change is not required to test this — the judged span is observable on its own.

**Acceptance Scenarios**:

1. **Given** an agent run that creates new files and records none of them, **When** the task reaches its gates, **Then** the judged span contains those files and their contents.
2. **Given** an agent run that records all of its work itself, **When** the task reaches its gates, **Then** the judged span is unchanged from today's behaviour.
3. **Given** an agent run that records some work and leaves other files unrecorded, **When** the task reaches its gates, **Then** the judged span contains both.
4. **Given** an agent run that changes nothing at all, **When** the task reaches its gates, **Then** the empty span is reported as an explicit "no change" outcome and is never presented to a gate as a change that passed.

---

### User Story 2 — What ships is what was judged (Priority: P2)

As the operator, I need the artifact that reaches the PR to be the same artifact the gates approved, provably — not a second computation that happens to agree.

**Why this priority**: P1 closes today's hole by making one of the two computations complete. P2 removes the second computation, which is what stops the two views drifting apart again in some new way — the amend path, deletions, submodules. Valuable alone, but only after P1 exists to produce the artifact.

**Independent Test**: Compare the span a gate judged against the span the delivery published for the same task; they must be the same object, not merely equal-looking.

**Acceptance Scenarios**:

1. **Given** a task that passed its gates, **When** it is delivered, **Then** the published content is the exact span that was judged, identified by the same reference.
2. **Given** a task whose gates judged a span, **When** delivery runs, **Then** delivery performs no independent discovery of what changed.
3. **Given** a change reported as N files and M lines by the gate-time projection, **When** the resulting PR is inspected, **Then** it carries the same N files and M lines.

---

### User Story 3 — Retire the mechanisms that existed to compensate (Priority: P3)

As a maintainer, I need the compensations built around the old guess removed, so the next reader does not inherit machinery whose reason has gone.

**Why this priority**: Pure debt removal, and only safe once P1 and P2 hold. Skipping it leaves the codebase carrying two explanations of the same thing — the exact condition the demolition epic (#611) exists to stop.

**Independent Test**: Each retired mechanism has a test that used to justify it; after removal the behaviour it protected is still covered by the new single artifact.

**Acceptance Scenarios**:

1. **Given** the change is materialized before judging, **When** the diff collector runs, **Then** its multi-step fallback ladder (which exists only to guess what state the agent left behind) is gone or independently justified.
2. **Given** the post-run advisory check that reports whether the repo guide was updated, **When** a run creates that guide for the first time, **Then** the check does not report it as untouched.
3. **Given** the worker skill instruction to record one's own work, **When** it is read after this change, **Then** it presents itself as guidance on writing a good message and naming judgment calls, and no longer as the thing that makes verification correct.

---

### Edge Cases

- **The agent changed nothing.** A first-class, distinguishable outcome: the task settles successfully, publishes nothing, and is reported upstream as no progress — so it feeds the existing no-progress watchdog instead of reading as a delivered increment. This keeps legitimate no-ops (an idempotent re-run, a bug already fixed) from becoming operator noise, while making it impossible for "the agent accomplished nothing" to masquerade as work. Read-only task kinds are unaffected and stay successful. (Resolved 2026-08-22.)
- **A gate rejects the change and the task retries.** The agent keeps iterating in the same workspace, as today. The task's pre-run reference stays pinned where it started, so each attempt is captured fresh and judged IN FULL against that original point — nothing carries forward unjudged. The rejected artifact is superseded, never published. (Resolved 2026-08-22.)
- **A run is paused mid-flight for a usage limit and resumed later.** Work in progress is deliberately preserved across a pause; materialization must not discard or prematurely finalize it.
- **Files the repository is configured to ignore.** These are not published today, so they must not be judged either — the two views must agree on exclusions as well as inclusions.
- **The workspace is not a repository, or repository commands fail.** Collection is best-effort today and degrades to an empty result rather than failing the task. Under this change an empty result is no longer safely equivalent to "no change", so the failure must be reported as a failure to determine the change, not as an empty change.
- **Long-lived goals accumulating increments on a shared branch.** Successive materialized spans must remain individually identifiable rather than merging into an indistinguishable whole.
- **Binary and very large files.** The judged span must represent them without the projection under-reporting or the gate input becoming unusable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST produce, at the moment the agent's run ends, a single durable artifact representing everything the agent changed in the workspace, including files the agent did not record itself.
- **FR-002**: Determining that artifact MUST be mechanical. No instruction to the agent may be load-bearing for its completeness.
- **FR-003**: Every consumer that needs to know what the agent changed — each quality gate, the change-size projection, and any advisory check — MUST read that one artifact rather than deriving its own.
- **FR-004**: The artifact MUST be identified by a stable reference recorded on the task, alongside the already-recorded pre-run reference, so the change is expressible as a range between two points.
- **FR-005**: Publishing a change MUST publish the artifact that was judged, and MUST NOT perform its own discovery of what changed.
- **FR-006**: An empty artifact MUST be reported as an explicit "no change" outcome, distinguishable from both a successful change and a failure to determine the change.
- **FR-007**: A failure to determine the change MUST be loud and MUST NOT be presented to any gate as an empty change.
- **FR-008**: A run in which the agent records all of its own work MUST behave exactly as it does today, with no additional commits, no altered message, and no extra history.
- **FR-009**: The change-size projection MUST report the same span that is published; where it reports a bounded or partial view it MUST say so.
- **FR-010**: The system MUST NOT satisfy these requirements by making the existing diff step merely *see* unrecorded files while leaving two independent computations in place. (See Rejected Alternatives.)
- **FR-011**: Read-only task kinds MUST remain able to complete successfully while changing nothing.
- **FR-012**: A gate rejection followed by a retry MUST leave the workspace intact so the next attempt iterates on its own output, and MUST keep the task's pre-run reference pinned to its original value.
- **FR-013**: Each attempt MUST be captured and judged in full against that original pre-run reference. No content may reach publication having been judged only as a delta against a rejected attempt.
- **FR-014**: A code-writing task that produces an empty span MUST settle successfully with an explicit no-change outcome, MUST publish nothing, and MUST be reported to the goal layer as no progress rather than as a delivered increment.

### Key Entities

- **Pre-run reference**: the recorded state of the workspace before the agent runs. Already exists on the task.
- **Post-run reference**: the recorded state after the agent stops and everything it changed has been captured. New.
- **The judged span**: the difference between those two references. The single answer to "what did the agent change?", consumed by gates, the projection, and publication.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any completed task, the set of files a gate judged is identical to the set of files that reach the resulting PR — no exceptions, including changes made entirely of new files.
- **SC-002**: Re-running the recorded 2026-08-22 scenario (three new unrecorded files plus one modified recorded file) yields a judged span of 4 files and +179 lines, matching what is published, instead of 1 file and +32 lines.
- **SC-003**: A change consisting only of new unrecorded files can no longer pass any gate on an empty span.
- **SC-004**: The number of independent computations of "what the agent changed" in the system is exactly one, demonstrable by there being a single place that answers the question.
- **SC-005**: Deleting the worker instruction that asks the agent to record its own work does not change any gate verdict — proving the instruction is no longer load-bearing. (A thought experiment for review, not a shipped change.)
- **SC-006**: No regression in existing behaviour: the full test suite passes, and the zero-token guard tests are untouched.

## Rejected Alternatives

- **Make the existing diff step include unrecorded files** (intent-to-add before diffing). One line, and it does close the observed case. Rejected because it repairs *seeing* without repairing *agreeing*: two independent computations would remain, bound only by a comment, and the next divergence would simply be a different one. The defect is not that one computation has a blind spot; it is that there are two. Ruled by Denys, 2026-08-22: *"I don't want a workaround, I want a grown solution."* Recorded here because the cheap fix is the obvious one and will be re-proposed by anyone reading only the symptom.
- **Enforce the recording instruction harder** — sterner prompt wording, or failing the task when the agent leaves files unrecorded. Rejected: it keeps a correctness invariant inside a language model's compliance, which is the root cause rather than a remedy, and it fails runs that did nothing wrong.
- **Have each consumer collect its own complete view** (each gate stages and diffs independently). Rejected: it multiplies the computations rather than reducing them, and guarantees only that they are all complete today, not that they agree tomorrow.

## Assumptions

- Capturing the agent's output as a repository object is acceptable on every supported engine path, and where the workspace is not a repository the behaviour degrades to the loud "cannot determine" outcome in FR-007 rather than to silence.
- Where the capture happens — on the host after the sandbox exits, or inside the sandbox as the runner's final act — is an implementation decision for the plan, except that it determines whether the in-sandbox advisory check can read the artifact. If it cannot, that check moves rather than being taught the same trick twice (User Story 3).
- The existing per-task pre-run reference is a suitable starting point and does not need redefining.
- Publication continues to open a PR per change; this specification does not change what is published, only which artifact is published.
- This is a bug class rather than a design change, so no constitutional amendment is required. It strengthens Principles IV (single writer, applied to the definition of the change), V (a gate must not ship on its own silence) and VI (loud over silent), and is a direct application of VII (fix the class, not the instance).

## Dependencies

- Touches the task settle path and the publication path, so it collides with the demolition epic (#611) and should sequence after spec 012.

## Resolved Questions

Both answered by Denys on 2026-08-22 and encoded above. Recorded here because a
decision that exists only in conversation is the failure mode this discipline
exists to prevent.

- **Retry semantics** → keep the tree, keep the original base. The agent keeps
  iterating in place; the pre-run reference stays pinned; every attempt is judged
  in full against it (FR-012, FR-013). *Rejected:* resetting the workspace between
  attempts, which discards work the agent may have got mostly right and changes
  the retry loop from "fix your own output" into "rewrite from scratch"; and
  promoting the rejected artifact to the new base, which would let
  gate-REJECTED content reach a PR without being re-judged — reintroducing this
  spec's own bug class.
- **Empty change for code-writing kinds** → settle done, flagged no-change,
  counted as no progress (FR-014). *Rejected:* failing the task, which punishes
  runs that were correct to do nothing; and plain success, which is the
  false-green being closed — an upstream poller cannot distinguish "nothing
  needed doing" from "the agent accomplished nothing".
