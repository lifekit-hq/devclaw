# Spec 027 — Instance health drift detection

**Status:** implementing [US1]
**Issue:** lifekit-hq/devclaw#596
**Sized:** spec (touches heartbeat, new module, config, tripwire tests)

---

## What

Surface instance health drift so degradation is visible without shelling into the
host. On 2026-08-22 the VPS reached 79% disk with 20 orphaned docker volumes and
34 stale workspace directories; not one of those facts appeared on any devclaw
surface. The natural home is the goal-layer heartbeat as a zero-LLM scheduled
edge (joining cycle report and self-deploy), with the problems catalog as the
breach record so a drift condition dedupes and ages the same way every other
failure does.

**Deliberately NOT a janitor.** This spec observes and reports. Deletion belongs
to the owner of the resource under issue #595.

---

## Context

`host_resources.py` already records the doctrine:

> Making that blindness visible — reporting the divergence between what is recorded
> and what is present — is a separate, read-only job (issue #596) that must never
> be allowed to delete anything.

The cognition-prompts rule warns that new per-tick work goes after phase gates and
ideally avoids subprocesses on idle paths. Solution: a rate-limited gate (meta key
`health_drift_last_check_ms`) so the docker subprocess runs at most once per
`DEVCLAW_HEALTH_INTERVAL_S` (default 1 h), not every 15-minute tick. The disk
probe (`shutil.disk_usage`) and sweep-count probe (SQLite) are cheap enough to
always run but are still guarded by the same interval gate for simplicity.

Rejected alternative: ops-agent (lifekit-stack repo, separate process). Keeping the
probe inside devclaw means it uses the same GoalStore, StateStore, and problems
catalog without any IPC, and the zero-LLM idle guard is maintained by the existing
scheduled-edge pattern.

---

## Requirements

### Functional

- **R1.** A new module `devclaw/goal/health_drift.py` provides three probe functions
  and a `run_health_drift_checks()` orchestrator. All are pure mechanism: no LLM,
  no write/delete.
- **R2.** Probe 1 — disk headroom: `shutil.disk_usage(goals_dir)` returns used %
  of the workspace filesystem. A probe failure returns `None` (unknown).
- **R3.** Probe 2 — orphaned docker volumes: `docker volume ls` filtered to
  `devclaw-toolchains-*`; count those not accounted for by any registered project
  workspace. A failure (daemon missing, timeout) returns `None` (unknown).
- **R4.** Probe 3 — stale workspaces: `host_resources.sweep_candidates()` count;
  terminal-goal workspace dirs eligible for sweep but still on disk. `None` on
  failure.
- **R5.** A breach of a configurable threshold calls `store.record_problem()` with
  `category="other"` and a kind: `disk_usage_high`, `orphan_docker_volumes`, or
  `stale_workspaces`. The problems catalog's fingerprint normalizer provides dedup
  so repeated ticks produce one row with an incrementing count, not N rows.
- **R6.** A probe returning `None` produces no record — unknown is not an alarm and
  is not a false all-clear.
- **R7.** A new `_maybe_check_health_drift()` method on `GoalService` is the 4th
  scheduled edge in `_loop()`, placed after the self-deploy edge, with its own
  `try/except` so a crash never kills the heartbeat.
- **R8.** The edge gates on meta key `health_drift_last_check_ms` so probes run at
  most once per `DEVCLAW_HEALTH_INTERVAL_S` (default 3600 s). The gate is a cheap
  timestamp compare; on idle ticks where the interval hasn't elapsed the edge
  returns immediately.
- **R9.** Four new env vars read ONLY through `devclaw/config.py` (single-doorway
  invariant): `DEVCLAW_HEALTH_DISK_WARN_PCT` (default 80), `DEVCLAW_HEALTH_ORPHAN_DOCKER_WARN`
  (default 10), `DEVCLAW_HEALTH_STALE_WS_WARN` (default 20),
  `DEVCLAW_HEALTH_INTERVAL_S` (default 3600). All documented in
  `docs/reference/env-vars.md`.

### Invariants preserved

- **Zero LLM.** `run_health_drift_checks()` contains no `claude` call. The
  `FakeClaude.calls == 0` guard tests remain green and unmodified.
- **Never deletes.** The module contains no `rmtree`, `remove`, or docker rm call.
- **Fail-closed probes, fail-open health.** A probe crash degrades to unknown (no
  problem, no exception). The heartbeat never dies.

### Named regression tests (done-when)

- **T1.** Healthy instance: all three probes return values below thresholds →
  `store.list_problems()` is empty.
- **T2.** Threshold breach: disk probe returns 85% (above 80% default) → exactly
  one `disk_usage_high` row after the first call; calling again with the same
  reading increments `count` on the SAME row (dedup confirmed).
- **T3.** Probe failure: all probes return `None` → no problem recorded, no
  exception raised.

---

## Story slices

### [US1] Instance health drift probe — the full scope (this PR)

All of R1–R9 and T1–T3. The entire spec is one coherent, reviewable unit: the
probe module, config, heartbeat hook, env-var docs, and tripwire tests ship
together.

**Done when:** `pytest` green (including T1–T3), `ruff check .` clean, `mypy`
clean, `test_env_vars_doc_sync` and `test_config_single_doorway` green (the
structural guards enforce R9 mechanically).
