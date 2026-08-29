# Feature Specification: Event-Driven Triggers — webhooks drive the state machine, the heartbeat demotes to fallback

**Feature Branch**: `023-event-driven-triggers`

**Created**: 2026-08-27

**Status**: ACTIVE — un-parked by Denys 2026-08-29 (the resume condition was
explicitly overridden alongside the unattended-week arc; spec 022's US1/US2/US3
core landed via #723/#727 the same day, with only the demolition scope
trailing as a pure-removal PR). Clarify session held 2026-08-29:

- Q: Ingress shape (the VPS is Tailscale-internal — GitHub can't reach it)?
  → A: **Public HTTPS endpoint** — one signature-verified webhook route,
  exposed via Tailscale Funnel scoped to the webhook path only; the rest of
  the HTTP surface stays internal. Relay-polling and fast-poll alternatives
  rejected.
- Design ruling recorded at plan time: events are a WAKE for the existing
  machinery (poke + trigger-named log), never a second transition path —
  the strongest possible form of FR-002's "the heartbeat is a complete
  fallback": both paths run literally the same code. Grading (US2) is the
  one place an event does direct work, spending exactly the cognition the
  manual verb spends (FR-007).

**Input**: Ruled direction from the 2026-08-27 architecture session: devclaw's storage is already event-sourced (append-only StateStore + projections) but its *triggering* is polled — a ~15-minute heartbeat scans goals, grading waits for manual verbs, completion evidence is re-derived on a timer. The established shape is GitHub-native webhooks driving the state machine, with the heartbeat demoted to what workflow engines call a fallback timer. Nothing invented: this is standard event-driven architecture over the tracker the work already lives in.

## Why (the ruled frame)

devclaw is an autonomous **Kanban pull system over GitHub-native events**: tickets
refined to a Definition of Ready, pulled by a durable workflow engine under WIP
limits, executed as jobs, verified by gates, done when the ticket closes. Spec 022
makes the ticket the identity; this spec makes the tracker's events the triggers.
The zero-token idle invariant is untouched and arguably strengthened: webhooks are
mechanism (zero LLM), cognition still fires only at decision points, and fewer
speculative timer-scans run at all.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tracker events advance goals immediately (Priority: P1)

When a PR delivering goal work is merged (or its referenced issue is closed), the
goal reacts *now* — the done-check fires on the event, not up to a heartbeat later.
When CI finishes on a delivery, the gate evidence arrives as the event's payload
instead of being re-derived by polling.

**Why this priority**: This is the largest legibility-and-latency win — the
operator merges a PR and sees the goal advance while they watch, which is also
what makes companion mode feel alive.

**Independent Test**: Merge a delivery PR on a live goal; observe the done-check
firing within seconds of the merge event rather than on the next tick.

**Acceptance Scenarios**:

1. **Given** a goal whose delivery PR is open, **When** the PR is merged,
   **Then** the goal's advance/done-check runs in response to the event, and the
   goal log names the event (not "tick") as the trigger.
2. **Given** a goal whose referenced issues are all closed by an event, **When**
   the last close event arrives, **Then** the goal proposes done without waiting
   for the heartbeat.
3. **Given** webhook delivery fails or is missed, **When** the next heartbeat
   runs, **Then** the same transition happens anyway — the timer is the fallback,
   and no transition exists that only an event can produce.

---

### User Story 2 - Refinement is event-driven and lives on the ticket (Priority: P2)

Opening or editing an issue triggers grading; the verdict lands **on the issue** —
the readiness label carries the state and a comment names exactly what is missing
when the verdict is `needs-refinement`. Editing the issue to address the gaps
re-triggers grading with no human verb. The refinement conversation has a home:
the ticket.

**Why this priority**: This resolves the "needs-refinement flow feels unclear"
smell — today the verdict lives in a tool response, nothing re-grades without a
manual call, and the fix-the-gaps conversation happens nowhere.

**Independent Test**: Open an underspecified issue; observe the label + gap
comment appear. Edit the issue to fill the gaps; observe the label flip to
`devclaw-ready` with no manual re-grade call.

**Acceptance Scenarios**:

1. **Given** a new issue on a registered project, **When** it is opened,
   **Then** grading runs and the readiness label + (on `needs-refinement`) a
   comment naming the missing elements appear on the issue.
2. **Given** a `needs-refinement` issue, **When** its body is edited, **Then**
   grading re-runs automatically and the label reflects the new verdict.
3. **Given** grading cognition is unavailable, **When** an issue event arrives,
   **Then** the issue is left ungraded with the failure recorded loud (fail
   closed: no `devclaw-ready` without a confident verdict — existing rule,
   unchanged).

---

### User Story 3 - The heartbeat demotes to a fallback timer (Priority: P3)

The heartbeat's role narrows to: catching missed events, driving genuinely
scheduled work (run windows, cadences, pause re-probes), and the watchdogs. Every
transition it drives is also event-drivable; nothing *waits* for the timer that an
event has already announced.

**Why this priority**: This is the cleanup that makes the model honest — it lands
last because it requires US1/US2's event paths to exist and be trusted first.

**Independent Test**: With webhooks healthy, observe a full goal lifecycle in
which no state transition names the tick as its trigger except scheduled/watchdog
work; disable webhooks and observe the same lifecycle complete correctly on timer
cadence alone.

**Acceptance Scenarios**:

1. **Given** healthy event delivery, **When** a goal runs end to end, **Then**
   tracker-driven transitions are all event-triggered and the tick contributes
   only scheduled/watchdog actions.
2. **Given** events are disabled or lost, **When** the system runs on the
   heartbeat alone, **Then** behavior degrades only in latency — never in
   correctness (every event-driven transition has its polled twin).

---

### Edge Cases

- **Duplicate or replayed webhook deliveries**: event handling is idempotent —
  the same event applied twice produces one transition (the CAS'd transition
  layer already guarantees this; the spec makes it a named requirement).
- **Events arriving for unknown/unregistered repos or goals**: dropped with a
  recorded reason, never an error loop.
- **Ordering**: an event arriving before its cause is visible via the API (e.g.
  merge webhook before the ref is fetchable) retries with backoff, then falls
  back to the timer path.
- **Security**: the webhook endpoint authenticates deliveries (shared-secret
  signature verification); unauthenticated posts are rejected and counted.
- **The zero-token invariant**: an event that requires no decision spends no
  cognition; grading (US2) spends exactly the cognition the manual verb spends
  today, just at a different trigger.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept authenticated GitHub webhook deliveries for
  the registered projects' repositories (issues, issue edits, labels, PR state,
  check runs, deployments).
- **FR-002**: Every tracker-driven goal transition MUST be executable from its
  event; the heartbeat MUST remain a complete fallback (no event-only
  transitions).
- **FR-003**: Event handling MUST be idempotent under duplicate delivery and
  safe under reordering; conflicts resolve through the existing CAS'd transition
  layer.
- **FR-004**: Issue opened/edited events MUST trigger grading; the verdict MUST
  land on the issue as label + (for `needs-refinement`) a comment naming the
  missing elements; grading keeps its fail-closed rule.
- **FR-005**: PR-merged and issue-closed events MUST trigger the goal's
  advance/done-check for affected goals immediately.
- **FR-006**: Check-run events MUST be usable as gate evidence where gates today
  poll for the same facts.
- **FR-007**: Event-driven paths MUST spend zero cognition except where the
  equivalent manual/polled path already spends it.
- **FR-008**: The goal log MUST name the trigger (event vs tick) for every
  transition, so the demotion is observable.
- **FR-009**: Webhook infrastructure failure MUST degrade to today's polled
  behavior loudly (a recorded problem entry), never silently.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Median delay from PR-merge to goal advance drops from tick-scale
  (minutes) to event-scale (seconds) with webhooks healthy.
- **SC-002**: An underspecified issue receives its readiness verdict and gap
  comment without any manual grading verb, and a subsequent edit re-grades it —
  zero operator round-trips through MCP tools.
- **SC-003**: With webhooks disabled, the full existing test suite's behavior is
  unchanged (the fallback is complete).
- **SC-004**: Idle cost stays ~0 cognition calls (the zero-token guard tests
  stay green, unchanged).

## Assumptions

- Spec 022 (issue-keyed one-lane dispatch) lands first; events act on
  issue-keyed work items.
- The VPS deployment can expose one authenticated inbound HTTPS endpoint for
  webhooks (the console/HTTP surface already exists to hang it on).
- GitHub App vs per-repo webhook configuration is an implementation choice for
  the plan phase, not a spec concern.
- The trend detector's owner-notification channel is out of scope here (being
  silenced separately — digest-only surface, tinyspec lane).
