# Feature Specification: A provider-side transient pauses and resumes — it never burns the dispatch cap

**Feature Branch**: `036-server-transient-pause`

**Created**: 2026-09-06

**Status**: Draft — US1 implemented 2026-09-06; US2/US3 specified, not implemented

**Input**: User description: "A 529 Overloaded burns the dispatch cap; a server-side transient should pause-and-resume like quota/auth (lifekit-hq/devclaw issue #817)"

## Why (context the requirements hang off)

On 2026-09-03, 13:39–14:15 UTC the API answered `529 Overloaded`
(`errorKind: server_error`) on every session start for ~35 minutes. Three
goals — `fs-429-counterparty-flows`, `lkc-23-stacked-input`,
`lkd-100-remove-sandbox-lore` — each spent BOTH of their dispatches on that
outage, did zero agent work, and parked on `mechanical:dispatch_cap`. Six
sessions burned; three goals needed a human `resume_goal`. The problems
catalog names the class exactly seven times: `task_fail | session/prompt
failed: Internal error: API Error: 529 Overloaded … (failed after 2
attempts)`.

The mechanism is a classification gap, not three bad goals. `loom/limits.py`
already recognises the wording — it classifies as `TRANSIENT` — but
`TRANSIENT` means *retry now, in-process*, and `PAUSING_KINDS` covers only
`RATE_LIMIT`, `QUOTA` and `AUTH`. So a provider outage takes the ordinary
task-failure path: retries in the same doomed minute, then `mark_failed`,
then a goal-level re-dispatch, then the cap.

From the goal's point of view a provider outage is indistinguishable from a
usage cap: nothing the worker did caused it, no retry within the outage can
succeed, and waiting is the only cure. Those are precisely the properties
the pause-and-resume brake was built for (one account-wide `paused_until`,
WIP preserved, zero tokens while paused, auto-resume, one owner ping). This
spec routes the class onto that brake, and takes the same care AUTH took
after the 2026-07-21 strong/weak split: an account-wide pause is an
expensive false positive, so only provider-shaped wording earns it.

## Clarifications

### Session 2026-09-06

Run without the owner present (autonomous dispatch on issue #817). Each
answer below is the default this spec adopts, with its reason; the owner
overturns any of them by amending the issue, which re-enters the pipeline.

- Q: Does the whole `TRANSIENT` class become pausing, or only the provider-side part? → A: Only the provider-side part, as a NEW kind `SERVER_ERROR`. `TRANSIENT` keeps its retry-now meaning for local blips (timeouts, `ECONNRESET`, signal death / OOM-kill of `claude --print`) — those recover in seconds and pausing the account for them would cost throughput for nothing.
- Q: Which wording is "provider-shaped" enough to pause the whole account? → A: The strong/weak split AUTH already established. STRONG (pausing): `529`, `overloaded` / `overloaded_error`, an explicit `server_error` error-kind token, and the harness's own `API Error: 5xx` framing. WEAK (still retry-now `TRANSIENT`): bare 5xx prose such as `503 Service Unavailable` or `502 Bad Gateway`, which also shows up in review feedback about the app under development.
- Q: Should a cognition call (`claude --print`) pause on the first 529? → A: No — it keeps its in-process retry budget (`COGNITION_MAX_RETRIES`) first, and pauses only when those are exhausted. A cognition retry costs seconds; a dispatched session costs a sandbox. The two lanes make opposite trade-offs on purpose.
- Q: Should a dispatched task retry inside the outage before pausing? → A: No. The task-side attempt is expensive and the pause path already preserves WIP and requeues, so the FIRST provider-shaped failure pauses. This is what removes the "(failed after 2 attempts)" burn.
- Q: How long is the pause? → A: Short, escalating, bounded — 5 min, doubling per consecutive episode probe, capped at 30 min. Rationale: the pause path's existing bound is `MAX_PAUSE_REQUEUES = 5` requeues per task; 5/10/20/30/30 covers ~95 min of outage inside that bound, where a flat 5 min covers only 25 min — less than the incident that produced this spec. A stated `Retry-After` in the provider's own text still wins.
- Q: Is the owner ping the same one quota sends? → A: Same ONCE-per-episode machinery, different words. "paused on a usage limit" would be a false statement about the account; a provider outage is weather, so it pings at OWNER level without the `critical` pierce that AUTH earns (there is nothing for a human to do).
- Q: Does a `SERVER_ERROR` pause count against the goal's dispatch cap? → A: Not beyond the one charge the dispatch already made, and that one stays refundable. `actions_dispatched` is incremented when the session is dispatched (`tick_dispatch`) and refunded on a productive settle (`tick_settle`). The pausing branch requeues the task instead of settling it, so the SAME dispatch resumes after the outage and can still earn its refund. The failure path instead settled `failed` (charge sticks, no refund) and let the goal dispatch again — that is how a 35-minute outage consumed a 2-dispatch budget without any agent work.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A provider outage pauses instead of failing (Priority: P1)

A dispatched session dies at start with `API Error: 529 Overloaded`. Instead
of retrying inside the outage and settling the task `failed`, devclaw sets
the account-wide pause, snapshots the workspace WIP, and requeues the task.
The goal keeps its in-flight ref and its dispatch budget; the owner hears one
accurate ping; when the pause elapses the task runs again with no human verb.

**Why this priority**: This is the fix. Without it every other refinement is
tuning of a brake that never engages.

**Independent Test**: Drive a task whose engine result is the observed 529
wording; assert the task is requeued (not failed), the global pause is set
with a `server_error` reason, and the engine ran once — no settle, therefore
no further dispatch charge.

**Acceptance Scenarios**:

1. **Given** a dispatched task, **When** the engine returns `session/prompt failed: Internal error: API Error: 529 Overloaded`, **Then** the task is requeued, dispatch is paused account-wide, and no second attempt is made inside the outage.
2. **Given** the pause is active, **When** the heartbeat ticks, **Then** every goal reports `RATE_LIMITED`, zero `claude` calls are made, and the owner is pinged exactly once with provider-outage wording (not "usage limit").
3. **Given** the pause has elapsed, **When** the next tick runs, **Then** the pause is cleared, the owner hears one resume ping, and the requeued task dispatches again.
4. **Given** review feedback containing `expected 200 got 503`, **When** it is classified, **Then** it stays a retry-now/real failure — the account is never paused on app-domain prose.

---

### User Story 2 - A sustained outage escalates, bounded (Priority: P2)

Consecutive provider-outage pauses within one episode back off further each
time — 5, 10, 20, 30, 30 minutes — so a 35-minute outage costs a handful of
cheap probes instead of a probe every five minutes, while a one-off blip
still resumes in five. The escalation resets once work succeeds again.

**Why this priority**: US1 alone covers ~25 minutes of outage before the
per-task requeue bound fails the task — less than the incident that produced
this spec. Escalation is what makes the brake hold for a real outage.

**Independent Test**: Set three successive `SERVER_ERROR` pauses inside one
episode and assert the backoff sequence doubles and clamps; then settle a
task successfully and assert the next pause starts at the base again.

**Acceptance Scenarios**:

1. **Given** a first provider-outage pause, **When** the probe after it fails the same way, **Then** the second pause is twice the first, and later ones clamp at the 30-minute ceiling.
2. **Given** an escalated episode, **When** a session completes normally, **Then** the episode ends and the next provider outage starts from the 5-minute base.
3. **Given** the provider states its own `Retry-After`, **When** the pause is computed, **Then** the stated hint wins over the escalation ladder.

---

### User Story 3 - The sandbox reports the outage structurally (Priority: P3)

The in-sandbox runner already tags a clear usage/rate limit as
`status="rate_limited"` so the host does not have to regex nested agent
wording. A provider outage gets the same belt-and-suspenders: the runner tags
`status="server_error"` with any stated `retry_after`, and the host trusts the
tag when present while keeping the regex fallback for anything untagged.

**Why this priority**: Detection currently depends on the 529 wording
surviving intact through ACP → runner → engine → settle. It did in the
observed incident, but a re-worded agent error silently returns the class to
the cap-burning path. Structural beats textual — after the pause exists.

**Independent Test**: Have the fake ACP agent fail with 529 wording; assert
the runner's terminal result carries `status="server_error"` and the host
pauses on the tag alone (wording stripped).

**Acceptance Scenarios**:

1. **Given** the agent fails with provider-overload wording, **When** the runner emits its terminal result, **Then** `status` is `server_error` and the original error text is preserved.
2. **Given** a result tagged `server_error` whose text carries no recognisable wording, **When** settle classifies it, **Then** it still pauses.

---

### Edge Cases

- A 529 whose text ALSO carries usage-limit wording classifies as QUOTA: the pausing kinds keep their existing priority order, so a real cap is never swallowed by the weaker outage policy.
- A single task riding the pause loop past `MAX_PAUSE_REQUEUES` still fails with the real reason — the bound is unchanged; only the reason wording gains the outage case.
- An outage that begins mid-session (agent already working) is unchanged by this spec: the WIP snapshot on the pause path preserves the tree exactly as it does for a quota hit.
- `529` inside a worker's own test output (a project whose API returns 529) reaches the classifier only as a task FAILURE string; the strong pattern is provider-shaped (`API Error: 529`, `overloaded`), and a bare assertion number stays REAL, exactly as the existing patterns treat `500`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The failure classifier MUST recognise a provider-side server error as its own kind, distinct from both `TRANSIENT` (retry now) and `QUOTA`/`RATE_LIMIT` (usage caps).
- **FR-002**: That kind MUST be a pausing kind: it sets one account-wide `paused_until` shared by the task queue and the heartbeat.
- **FR-003**: Only provider-shaped wording MUST classify as the pausing kind. Ambiguous 5xx prose MUST keep today's `TRANSIENT`/`REAL` treatment, so app-domain feedback can never pause the account.
- **FR-004**: A dispatched task hitting the kind MUST be requeued with its WIP snapshotted, not retried inside the outage and not settled `failed` — so the outage consumes no ADDITIONAL dispatch and the one already charged stays refundable when the resumed session settles productively.
- **FR-005**: Host cognition MUST retry the kind in-process up to its existing retry budget before the failure propagates to the pause path.
- **FR-006**: While paused, the heartbeat MUST make zero `claude` calls and report every goal `RATE_LIMITED` (the existing zero-token guard, unchanged).
- **FR-007**: The owner MUST be pinged exactly once per pause episode, worded as a provider outage — never as a usage limit and never as an auth failure. The resume ping MUST match the pause's wording.
- **FR-008**: The pause MUST expire on its own and work MUST resume with no human verb.
- **FR-009** *(US2)*: Consecutive pauses within one episode MUST escalate geometrically from a short base to a bounded ceiling; a stated provider `Retry-After` MUST take precedence.
- **FR-010** *(US2)*: A successful session MUST end the episode, so the next outage starts from the base.
- **FR-011** *(US3)*: The runner MUST tag a provider-overload terminal result structurally, and settle MUST honour the tag independently of the error text.

### Key Entities

- **`FailureKind.SERVER_ERROR`** — the new classification; member of `PAUSING_KINDS`.
- **Pause episode** — the account-wide `paused_until` + `pause_reason` + the once-per-episode `pause_notified` flag that already exist in `StateStore.control`; this spec adds only the new reason prefix (US1) and an episode escalation counter (US2).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A provider outage costs at most the one dispatch already in flight per goal, and that one is still refundable. On the 2026-09-03 incident shape (three goals, 35 minutes) the outcome is three goals paused and auto-resumed instead of six burned sessions and three human `resume_goal` calls.
- **SC-002**: One owner ping per outage episode, fleet-wide — not one per goal (3× reduction on the incident shape).
- **SC-003**: Zero `claude` calls while paused (the existing zero-token assertion holds unmodified).
- **SC-004**: No new class of account-wide pause from app-domain text: the classifier's REAL/TRANSIENT verdicts for 5xx assertion prose are byte-identical to today.

## Assumptions

- The provider wording reaching `classify_failure` retains at least one strong marker (`529`, `overloaded`, `server_error`, `API Error: 5xx`). US3 removes this assumption; until then it is the observed shape from the incident.
- `MAX_PAUSE_REQUEUES = 5` stays the per-task bound. This spec does not raise it; US2's escalation is what makes those five requeues span a realistic outage.
- The account-wide pause is the right blast radius: the outage is provider-side, so no goal on any project can make progress during it.

## Rejected alternatives (direction memory)

- **Make all of `TRANSIENT` pausing.** Rejected: `TRANSIENT` also carries local timeouts, `ECONNRESET` and signal-death OOM kills, which recover in seconds. Pausing the fleet for 5 minutes on a socket blip trades a small burn for a large stall, and folds two different cures (wait for the provider vs. retry the machine) into one policy.
- **Refund the dispatch cap after a provider-caused task failure.** Rejected: it fixes the accounting symptom while still spending the sessions. The sessions are the expensive part — six were burned in the incident, and refunding would have burned them again on the next tick.
- **Raise `MAX_PAUSE_REQUEUES` instead of escalating the backoff.** Rejected: more requeues at a flat 5-minute interval means more doomed probes for the same coverage; escalation buys the same span with fewer probes and leaves the bound meaning what it means for quota.
- **Let the worker's own agent retry through the outage.** Rejected: the retry happens inside one sandbox session against a wall-clock timeout, so a 35-minute outage eats the session's budget and then fails anyway — that is the observed `(failed after 2 attempts)` line.
