# Feature Specification: Autonomous Issue Claim & Dispatch (scorecard-gated)

**Feature Branch**: `feat/autonomous-issue-dispatch`

**Created**: 2026-08-15

**Status**: PARKED — direction memory only, not scheduled (parking block added 2026-08-29 by the spec-gap audit; the prior PARTIAL line over-claimed: `select_for_pickup()` in `devclaw/goal/self_issue.py` is the SELF-FIX pickup path, not this spec's claim mechanism — none of the load-bearing machinery here (operator flag, CAS'd claim marker, provenance wall, human promotion) exists in the tree, and no tasks.md was ever generated). Owner: Denys. Resume condition: the compounding scorecard's autonomy gate (`DEVCLAW_RATCHET_*`, spec 018) reads ready AND Denys rules the week-scale unattended runs (spec 025, first week starting 2026-09-01) earned the trust for self-selected work. Review by 2026-10-01 even if neither has. Run `/speckit-clarify` with Denys before any implementation

**Input**: User description: "P2 of the autonomous issue-driven pipeline arc — the heartbeat claims devclaw-ready issues and dispatches them via the existing create_goal path, but only behind a manually-flipped flag, with a hard provenance wall keeping self-filed work from self-executing and a human as the merge backstop. This is where autonomy turns on."

## Context & Motivation *(informative)*

P1 (spec 006) makes asks *gradeable* — every ask lands `devclaw-ready` or `needs-refinement`, with humans still dispatching. P2 closes the loop: the heartbeat itself claims `devclaw-ready` issues and dispatches them, so work flows from intake to execution without a human pulling each trigger.

This is the single most consequential slice — it is the autonomy switch. The 2026-08-15 governance research (GitHub Copilot at scale: ~17M agent PRs/month, ~1-in-10 legit, a platform kill switch, a maintainer revolt over untraceable auto-generated work) showed that the danger of an autonomous loop is not "can it code" but "what stops it feeding itself slop." So P2 ships the autonomy *together with* its brakes: a manually-flipped activation flag, a hard provenance wall between the filing queue and the execution queue, and the human as the merge backstop. Autonomy without those brakes is not a smaller version of P2 — it is the failure mode.

## Clarifications

### Session 2026-08-15

- Q: How does the autonomous-dispatch flag turn on? → A: **Manual flip by the operator.** Off by default; the operator flips it on when the scorecard has earned it. The scorecard informs the decision; it never auto-flips.
- Q: Who authorizes a devclaw-self-filed issue into the execute queue? → A: **Human promotion only.** External/human-filed ready issues auto-flow; self-filed ready issues need an explicit human promotion before they are claimable. Hard wall, no self-dealing.
- Q: Does P2 include devclaw asking its own clarifying questions? → A: **No — async-clarify is deferred.** P2 only claims issues already graded `devclaw-ready`; an under-scoped ask stays `needs-refinement` and a human sharpens it. The clarify-conversation protocol is its own later slice.
- Q: When several issues are ready, which is claimed first? → A: **Priority label, then oldest** (FIFO within a priority band), matching existing P0–P2 backlog conventions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The heartbeat claims and dispatches a ready issue (Priority: P1)

With the autonomy flag ON, the heartbeat finds the highest-priority, oldest `devclaw-ready` issue that is not held back by the provenance wall, claims it exactly once, and dispatches it through the existing goal-creation path referencing that issue. With the flag OFF (default), nothing auto-dispatches — behavior is identical to today.

**Why this priority**: This is the loop closing — the core value of the slice.

**Independent Test**: With the flag ON and one ready external issue present, verify it is dispatched within one tick cycle, exactly once, referencing the issue; with the flag OFF, verify nothing dispatches.

**Acceptance Scenarios**:

1. **Given** the flag is OFF, **When** ready issues exist, **Then** the tick claims and dispatches nothing (identical to pre-P2 behavior).
2. **Given** the flag is ON and multiple ready external issues exist, **When** the tick runs, **Then** it claims the highest-priority, oldest one and dispatches it via the existing goal path, under the dispatch cap.
3. **Given** an issue has been claimed, **When** a later tick runs, **Then** it is not claimed or dispatched again.

---

### User Story 2 - The provenance wall blocks self-dealing (Priority: P1)

A devclaw-self-filed `devclaw-ready` issue is **not** claimable by the tick until a human explicitly promotes it. Human/external-filed ready issues flow without that extra step. The same system can never both file and execute a piece of work.

**Why this priority**: This is the anti-busywork guarantee — the specific failure the governance research flagged. It is not optional and cannot be deferred, because turning on autonomy without it *is* the self-dealing machine.

**Independent Test**: File one issue with devclaw provenance and one with human provenance, both graded ready; with the flag ON, verify only the human-filed one is dispatched and the self-filed one waits until promoted.

**Acceptance Scenarios**:

1. **Given** a self-filed ready issue with no human promotion, **When** the tick runs with the flag ON, **Then** it is not claimed.
2. **Given** a human promotes that self-filed issue, **When** the next tick runs, **Then** it becomes claimable and dispatches.
3. **Given** a human/external-filed ready issue, **When** the tick runs with the flag ON, **Then** it is claimable without any extra promotion.

---

### User Story 3 - Claiming is guard-safe and zero-token (Priority: P2)

The tick's "is there claimable work?" check is a cheap local read gated before any cognition or network call. When the flag is off, or there is no claimable ready issue, the tick spends zero cognition and makes zero GitHub calls — the zero-token idle guarantee is preserved. The claim itself is a single-writer, compare-and-set operation so a tick and a human cannot double-claim the same issue.

**Why this priority**: Correctness/safety of the mechanism. Without it, autonomy would either break the zero-token invariant or double-dispatch.

**Independent Test**: Assert the idle/flag-off tick makes zero cognition calls and zero GitHub calls; simulate concurrent tick + human claim and assert the issue dispatches at most once.

**Acceptance Scenarios**:

1. **Given** the flag is off or no claimable ready issue exists, **When** the tick runs, **Then** it performs no cognition call and no GitHub poll.
2. **Given** two actors attempt to claim the same issue concurrently, **When** both run, **Then** exactly one succeeds and the issue is dispatched once.

---

### User Story 4 - Human merge backstop & separation of powers (Priority: P2)

Autonomously dispatched work still delivers as a PR that a human merges. devclaw cannot merge its own PR; existing quality gates and CI are unchanged. The claim moves the issue through a visible label state (in-progress → done/needs-human).

**Why this priority**: The last safety rail — even fully autonomous dispatch keeps a human at the merge boundary.

**Independent Test**: Verify a dispatched issue produces a PR, that devclaw does not self-merge, and that the issue's label reflects in-progress then its terminal state.

**Acceptance Scenarios**:

1. **Given** an autonomously dispatched issue, **When** work completes, **Then** it is delivered as a PR for human merge, not self-merged.
2. **Given** dispatch occurred, **When** it is in flight, **Then** the issue carries an in-progress state and resolves to done (closed) or needs-human.

---

### Edge Cases

- **Flag ON but dispatch cap already reached**: the tick claims nothing new until capacity frees — no firehose.
- **A ready issue goes stale** (repo moved since grading): re-check groundability at claim time; if no longer groundable, bounce to `needs-refinement`/needs-human rather than dispatch blindly. *(Minimal re-check in P2; deeper staleness handling can follow.)*
- **Self-filed issue promoted then later edited**: promotion applies to the promoted state; a materially changed ask should require re-grading (P1) and re-promotion.
- **Usage-limit pause active**: the tick claims nothing (dispatch is already pause-gated); no change to the pause semantics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When the autonomy flag is OFF (the default), the tick MUST NOT auto-claim or auto-dispatch any issue; behavior MUST be identical to pre-P2.
- **FR-002**: The autonomy flag MUST be changeable only by an explicit operator action. No metric or scorecard value may auto-enable it.
- **FR-003**: When ON, the tick MUST select the claimable ready issue by priority label first, then oldest-first within a priority band.
- **FR-004**: A claimed issue MUST be dispatched through the existing goal-creation path, referencing the source issue.
- **FR-005**: A devclaw-self-filed issue MUST NOT be claimable until an explicit human promotion; human/external-filed ready issues MUST be claimable without additional promotion.
- **FR-006**: The claim MUST be a single-writer compare-and-set operation with a local authoritative marker; the GitHub label mirrors it. Concurrent claimants MUST NOT double-dispatch.
- **FR-007**: The claim-eligibility check MUST be cheap and gated BEFORE any cognition or network call. On flag-off or no-claimable-work, the tick MUST make zero cognition calls and zero GitHub calls (zero-token idle guard preserved).
- **FR-008**: Autonomously dispatched work MUST deliver via the existing PR path; devclaw MUST NOT merge its own PR; existing gates/CI MUST be unchanged.
- **FR-009**: Autonomous claims MUST respect the existing dispatch/concurrency cap — no separate uncapped path.
- **FR-010**: A claimed issue MUST transition through a visible label state (in-progress) and resolve to a terminal state (closed-done or needs-human).
- **FR-011**: At claim time, an issue that can no longer be grounded against the current repo MUST be re-routed (needs-refinement/needs-human), not dispatched blindly.

### Key Entities *(include if feature involves data)*

- **Autonomy Flag**: per-instance, default OFF, operator-only toggle.
- **Claim Marker**: local, single-writer, CAS'd record that an issue is claimed (authoritative); label mirrors it.
- **Provenance**: filed-by attribution on the issue (devclaw-self vs human/external) + a human-promotion signal for self-filed.
- **Triage Order**: priority-label band, then filing age.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the flag OFF, idle-tick token cost and behavior are identical to pre-P2 (zero-token idle guard test stays at 0 cognition calls).
- **SC-002**: 0 self-filed issues are dispatched without a human promotion.
- **SC-003**: 0 double-dispatches — an issue is dispatched at most once even under concurrent tick+human claiming.
- **SC-004**: With the flag ON and a claimable ready issue present, it is dispatched within one tick cycle, subject to the dispatch cap.
- **SC-005**: 0 self-merges — devclaw never merges its own PR.

## Assumptions

- The activation flag is a single per-instance switch (the account-wide posture), consistent with the existing pause/run-window controls.
- "Provenance" reuses the intake-recorded `asker`/`channel` attribution from P1/file_intake; self-filed = agent-authored via the intake tool.
- Human promotion is a lightweight signal (a label/action), not a new UI.
- Dispatch continues to use the existing goal path and its cap; P2 adds the *claiming*, not a new executor.

## Out of Scope *(named for later slices)*

- **Async-clarify** — devclaw asking its own clarifying questions via issue comments. (Deferred; own slice.)
- **Speckit execution binding** — P2 dispatches via whatever the current execution path is; making execution run through speckit is P3 (spec 008).
- **Delta-spec model, dedup, per-goal compute-budget** — mechanical governors, folded into P3/later.

## Rejected Alternatives *(direction memory)*

- **Auto-enable the flag on a scorecard threshold.** Rejected: the system deciding its own graduation is the self-grading risk the governance research named. The human flips it.
- **Independent-evaluator promotion of self-filed issues.** Rejected for P2: an agent vouching for another agent's work is a weaker guarantee than a human wall, and the human wall is the whole anti-busywork point.
- **Poll GitHub every tick for ready issues.** Rejected: breaks the zero-token idle guard. A cheap local marker is the tick signal; GitHub is the human-visible mirror.
- **Smallest/cheapest-first triage.** Rejected: starves important work and invites gaming the scorecard.

## Constitution Alignment *(no amendment required)*

- **Zero-token idle guard** — claim check gated before cognition/network (FR-007).
- **Single-writer state** — CAS'd claim marker is authoritative, label mirrors (FR-006), consistent with the GoalStore CAS discipline.
- **Done is a proposal; human merge backstop** — no self-merge (FR-008).
- **Loud failure over silent degradation** — stale/ungroundable at claim bounces visibly (FR-011).
