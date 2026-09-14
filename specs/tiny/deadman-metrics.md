# TinySpec: restore `/metrics`, the dead-man signal the v2 rewrite dropped

**Branch**: fix/metrics-deadman-restore
**Date**: 2026-09-14
**Status**: done
**Complexity**: small
**Issue**: #911 (the v1 tinyspec of the same name is `git show v1.2.0:specs/tiny/deadman-metrics.md`)

## North-star case

- **Failure moved**: *stopped when it shouldn't* — the only way the box can
  tell "devclaw is dead or hung" from "devclaw is quiet" is this scrape;
  without it a crash past the compose restart budget or a wedged tick loop
  goes dark until the owner notices.
- **Number that shows it**: `up{job="devclaw"}` on the box's Prometheus,
  0 since the 2.0.0 deploy (2026-09-13 14:44Z) against a healthy process;
  Grafana pages Telegram every few minutes.
- **Cut when**: the box stops running Prometheus + Grafana as its watcher
  (then the scrape job, the two rules, this route and the runbook lines go
  in one arc — issue #911 option 2).

## What

#849 (2026-09-06) made `/metrics` the dead-man signal and retired the
ops-agent watchdog. #909 (v2) deleted `devclaw/server/routes/metrics.py`,
its import in `http.py` and `tests/test_deadman_metrics.py` without a
decision — the lifekit-stack scrape job, the Grafana rules, the compose
comment and the self-deploy runbook all still expect it. This restores the
route over the v2 vitals `/health` already serves and opens it in the auth
gate, since the scrape carries no bearer token.

## Context

| File | Role |
|---|---|
| `devclaw/server/routes/metrics.py` | The route: seven gauges, hand-rendered; `render_metrics` is the pure half |
| `devclaw/server/http.py` | Registration is an import side effect — the missing line was the whole outage |
| `devclaw/server/lifecycle.py` | `OPEN_PATHS = {/health, /metrics}`: the two token-free liveness reads |
| `devclaw/server/routes/control.py` | `_vitals()` — the same tick/dispatch/pause facts, as JSON |
| `deploy/docker-compose.devclaw.yml`, `docs/runbooks/devclaw-self-deploy.md` | Already describe the scrape; unchanged |

## Requirements

- `GET /metrics` answers 200 with `text/plain; version=0.0.4` and no token.
- `devclaw_tick_age_seconds` = seconds since the last tick, since process
  start when none yet, `NaN` when neither — never a faked number.
- `devclaw_tick_seconds`, `devclaw_dispatch_open`, `devclaw_pause_active`,
  `devclaw_tasks_running`, `devclaw_build_info{version,git_sha}` as before.
- `devclaw_goals{state}` uses the v2 state word the console shows
  (`new · running · waiting · interrupted · blocked · proposed done ·
  achieved · cancelled`); every state renders, zero included. v1's
  `{phase}` label is gone with the v1 phases — no rule or dashboard read it.

## Rejected alternatives

- **Retire the signal instead** (#911 option 2): it is the box's only
  liveness read for devclaw now that the ops-agent is gone; nothing in 046
  argued for going dark.
- **`prometheus_client`**: still a dependency for seven static gauges.
- **Keep `/metrics` behind the token**: the scrape job has no bearer, and the
  page carries nothing secret — a 401 is a blind watcher with a healthy
  process, the same outage in a different status code.

## Tasks

- [x] Restore `routes/metrics.py` over v2 vitals; import it in `http.py`
- [x] Open `/metrics` in `AuthMiddleware` alongside `/health`
- [x] `tests/test_deadman_metrics.py`: tick-age rules, every state renders, route registered + open
- [x] `docs/reference/env-vars.md`: `DEVCLAW_TOKEN` exempts `/health` and `/metrics`

## Done When

- [x] Suite green; ruff, mypy, lint-imports clean
- [ ] Live: `up{job="devclaw"} == 1` on the box after the self-deploy; the Telegram alert resolves
