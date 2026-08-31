# Plan — spec 027 instance health drift

## [US1] Single PR — probe module + config + heartbeat + tests

### Files touched

| File | What changes |
|------|-------------|
| `devclaw/goal/health_drift.py` | NEW — four probe fns + `run_health_drift_checks()` orchestrator |
| `devclaw/config.py` | 5 new call-time accessors for `DEVCLAW_HEALTH_*` vars |
| `devclaw/goal/service.py` | 4th scheduled edge `_maybe_check_health_drift()` in `_loop()` |
| `docs/reference/env-vars.md` | 5 new rows in a new "Instance health" section |
| `tests/test_health_drift.py` | NEW — T1/T2/T3/T4 named regression tests |
| `specs/027-instance-health-drift/` | spec.md, plan.md, tasks.md |

### Key choices

- **Module location `goal/health_drift.py`**: the probe is a goal-layer scheduled
  edge so it lives beside the other goal-layer mechanism modules (`cycle_report.py`,
  `self_deploy.py`). Not in `host_resources.py` — that module owns deletion; the
  probe is read-only and lives separately.
- **Rate-limit gate via meta key**: mirrors the cycle-report's `cycle_report_exists`
  idempotency gate. One meta key `health_drift_last_check_ms`, cheap timestamp read
  on every tick, subprocess only when interval has elapsed.
- **`asyncio.to_thread` for the full check**: the docker probe is blocking (subprocess).
  Running the whole `run_health_drift_checks()` in a thread keeps the event loop
  unblocked, same as how `_refresh_pr_ledger` uses `to_thread`.
- **Probes injectable at module level**: the probe functions (`_disk_used_pct`,
  `_orphan_docker_volume_count`, `_stale_workspace_count`) are module-level
  callables so tests can monkeypatch them directly without subprocess/filesystem.
- **No ratchet on `sweep_candidates`**: we call it with no `batch_limit` cap to get
  the full count of eligible workspaces (not just the next batch to sweep).
