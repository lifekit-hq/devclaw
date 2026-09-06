# TinySpec: `/metrics` is the dead-man signal; the ops-agent is retired

**Branch**: feat/metrics-deadman
**Date**: 2026-09-06
**Status**: done (the alert itself lands in lifekit-stack — see "Going live")
**Complexity**: small

## What

devclaw reports everything it can see about itself: the notifier pings on
every block, pause, verdict and watchdog; doctor, the scorecard and the JSON
routes are the read side; the compose healthcheck restarts a container whose
`/health` stops answering. What it structurally cannot report is **its own
death** (a crash loop past `on-failure:5` goes dark with no ping) or a **hung
heartbeat** (HTTP answers, the tick loop does not). `/health` has carried
`last_tick_at` "for the external dead-man watcher" since #494. That watcher
never existed: the ops-agent read devclaw's generated files, never `/health`,
re-decided with an LLM what devclaw had already pinged, and after #844 its
only working action was an LLM choosing to `docker restart devclaw-mcp`
through a root-equivalent socket — which the healthcheck already does,
mechanically, 480× faster. Three of its four detectors were inert (one
predicate could never match a real phase; one read a file whose producer was
deleted; the tool it called was deleted).

This tinyspec:

1. **`/metrics`** (`devclaw/server/routes/metrics.py`): Prometheus exposition
   over projections `/node.json` already serves — `devclaw_tick_age_seconds`
   (seconds since the last tick, since process start when none yet, `NaN`
   when neither is known), `devclaw_tick_seconds`, `devclaw_dispatch_open`,
   `devclaw_pause_active`, `devclaw_goals{phase}`, `devclaw_tasks_running`,
   `devclaw_build_info{version,git_sha}`. Hand-rendered: seven gauges with
   safe label values are a dozen lines; no new dependency, no new container.
2. **The ops-agent stanza is deleted** from `deploy/docker-compose.devclaw.yml`
   (with it: the docker socket mount, the read-only state mounts, the
   incident dir). The lifekit-stack build stub and package go in that repo's
   own PR.

## Where the alert lives

Not here. The box already runs Grafana 11 + Prometheus 2.54 + Loki 3
(currently under finance-sentry's compose; ruled 2026-09-06 to move to
lifekit-stack as box infrastructure). Prometheus scrapes `/metrics` over
`lifekit-shared`; two provisioned Grafana rules alert to a Telegram contact
point directly, bypassing the notify-relay (a dead relay swallowed every
ping silently before): `up{job="devclaw"} == 0` for 3 min (dead / crash-
looping) and `devclaw_tick_age_seconds > 3 * devclaw_tick_seconds` while
`devclaw_dispatch_open == 1` for 5 min (hung; "held" is not "stalled").

The pairing rule: devclaw reports what it can see; the watcher covers only
what devclaw cannot, and depends on nothing devclaw writes except this one
scrape. The ops-agent violated exactly that.

## Rejected alternatives

- **Repair the ops-agent.** Delete three detectors, add a notifier, keep the
  LLM: the result is the dead-man watcher with an LLM bolted on, plus a
  root-equivalent docker socket for a restart the healthcheck already does.
- **A sixty-line custom watcher container.** Proposed first; withdrawn once
  the box turned out to run the standard stack already. Constitution IX: a
  devclaw-specific mechanism needs a reason the standard one cannot give.
- **Gatus / Uptime Kuma.** A fourth tool when Grafana alerting does the same
  with what is deployed; Uptime Kuma is UI-configured, against the GitOps
  rule.
- **`prometheus_client`.** Correct and standard, but a dependency for seven
  static gauges; revisit the day a histogram or a counter appears.

## Test

`tests/test_deadman_metrics.py` — the signal the alert reads (tick age from
last tick, from start when none, NaN when neither; dispatch/pause samples;
every phase renders) and the registration guard (a route module http.py
forgets to import serves nothing). Fail-closed/observability class: a broken
signal is a blind watcher.

## Going live

- devclaw: merge → self-deploy. `/metrics` answers on the box; the ops-agent
  container disappears on the next `compose up` (Denys stopped it by hand
  2026-09-06 18:45 UTC).
- lifekit-stack: the observability trio + Prometheus scrape job + Grafana
  contact point and rules (separate PR, same day).
- finance-sentry: sheds the trio, joins `lifekit-shared` (separate PR).

## Done When

- [x] `/metrics` renders the seven gauges; `devclaw_tick_age_seconds` follows the rules above
- [x] The ops-agent stanza, socket mount and incident dir are gone from the deploy compose
- [x] Runbook + INDEX say so; suite green, ruff + mypy + lint-imports clean
- [ ] Live: a Prometheus target `devclaw` is `up` and the two Grafana rules exist (lifekit-stack PR)
