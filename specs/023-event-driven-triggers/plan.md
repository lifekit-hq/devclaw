# Implementation Plan: Event-Driven Triggers (spec 023)

**Branch**: `feat/023-event-driven-triggers` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

## Summary

One authenticated webhook route + one mechanical event router. Events do not
grow a second state machine: a tracker event WAKES the existing machinery
(`GoalService.poke()` — the same in-process wake the MCP verbs already use)
after stamping a trigger-named goal-log line, so every event-driven
transition is executed by exactly the code the heartbeat would have run
minutes later (FR-002 by construction, not by parallel implementation).
Grading (US2) is the one direct action: an `issues opened/edited` event runs
the existing readiness grading for registered repos and lands the verdict on
the issue (label + gap comment), spending the cognition the manual
`regrade_intake` verb spends today (FR-007).

## Technical Context

**Touched**: `devclaw/server/routes/webhooks.py` (NEW — route + HMAC),
`devclaw/server/http.py` (registration — console stays LAST),
`devclaw/goal/events.py` (NEW — payload → actions, layer 2),
`devclaw/goal/service.py` (thin binding: expose the router with the service's
stores), `devclaw/intake.py` (reuse; add gap-comment posting if absent),
`devclaw/config.py` (+`DEVCLAW_WEBHOOK_SECRET`), docs (env-vars, runbook for
the Funnel wiring + GitHub webhook config), tests.

**Constraints**: constitution III (zero-token idle — an event with no
decision spends nothing; grading spends what the manual verb spends), IV
(events never write goal state directly — only log lines + poke; every state
mutation stays behind the tick/CAS), V/VI unchanged. Route-registration
order is load-bearing (`tests/test_route_shadowing.py`).

**Security**: HMAC SHA-256 (`X-Hub-Signature-256`) via
`hmac.compare_digest`; secret from `DEVCLAW_WEBHOOK_SECRET`; unset secret ⇒
the route answers 404 (feature off — fail-safe, no unauthenticated surface);
bad signature ⇒ 401, counted in a log line. Payloads for unregistered repos
are dropped with a recorded reason (202 + log).

## Design

### Route (`server/routes/webhooks.py`)

`POST /webhooks/github`: verify signature over the RAW body; parse the event
kind from `X-GitHub-Event`; hand `(event, payload)` to the goal layer's
router; always answer fast (202) — the router's work is bounded-mechanical
except grading, which is fired as a background task so GitHub's 10s delivery
timeout never sees cognition latency.

### Router (`goal/events.py`)

Pure decision table, injectable seams for tests:

| event | condition | action |
|---|---|---|
| `pull_request` | `action=closed, merged=true`, repo registered | goal-log the trigger on goals matching the repo (non-terminal), `poke()` |
| `issues` | `action=closed`, repo registered | same wake path |
| `check_run`/`check_suite` | `completed`, repo registered | same wake path (the done-gate's remote-checks read live state next tick) |
| `issues` | `action=opened/edited`, repo registered | grading: run the existing intake readiness grade for that issue; verdict lands as label (existing) + gap comment on `needs-refinement` (this spec); errors fail loud-and-recorded, never 5xx to GitHub |
| anything else / unregistered repo | — | drop, one log line with the reason |

Idempotency (FR-003): the wake path is naturally idempotent (a duplicate
poke is a no-op; the tick's CAS owns every transition). Duplicate grading of
an unchanged issue re-yields the same verdict — the same property the
manual regrade verb already has.

FR-008 (trigger naming): the goal-log line `event: <kind> <repo>#<n> —
advancing now (webhook)` lands before the poke; heartbeat-driven work keeps
its existing unmarked shape, so the demotion is observable per goal log.

### Heartbeat demotion (US3)

No cadence change in this PR — the heartbeat already only acts when there is
work, and with wakes landing at event time the tick becomes the fallback in
behavior. SC-001's latency claim is satisfied by the poke (seconds, not
minutes). A later cadence stretch is an ops knob, not code.

### Ops wiring (documented, applied at deploy)

- Tailscale Funnel scoped to the path: `tailscale funnel --https=443
  --set-path /webhooks/github http://127.0.0.1:18791/webhooks/github` — the
  rest of the HTTP surface stays tailnet-internal.
- GitHub side: one repo webhook per lifekit repo (issues, pull_request,
  check_run, check_suite) pointing at the funnel URL with the shared secret.
- Secret home: the compose env file (`DEVCLAW_WEBHOOK_SECRET`), same
  never-hand-edited contract as the other secrets.

## Constitution check

- III zero-token: PASS — wake path is subprocess-free and cognition-free;
  grading spends exactly the manual verb's cognition (FR-007).
- IV single-writer: PASS — the router writes goal-log lines only (append-only
  log is not CAS'd state) and pokes; every transition stays in the tick.
- V/VI: PASS — grading keeps its fail-closed rule; drops are recorded.
