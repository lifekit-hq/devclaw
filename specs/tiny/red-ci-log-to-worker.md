# TinySpec: a red CI verdict carries its log to the worker

**Branch**: feat/040-contract-to-actor (spec 040 US2, shrunk to this lane 2026-09-07)
**Date**: 2026-09-07
**Status**: done (approved by Denys 2026-09-07; implemented the same day)
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. A red rollup's correction says "read the failing check's log"; the sandbox holds no GitHub credential and cannot; the worker reports the gap, the goal parks on `mechanical:env`, a human pastes the log (four goals, 2026-09-07).
- **Number that shows it**: interventions per achieved goal (2.92 on 2026-09-07); `mechanical:env` holds naming CI-log access → 0.
- **Cut when**: a month of red rollups without a worker naming the log, and corrections converging without it.

## What

When `_autoheal_ci` (`devclaw/goal/tick_guards.py`) steers a red rollup back, the correction carries, per failing check, the last 120 lines of the failed job's log — read by the host through the same `gh` the rollup comes from, ANSI stripped, passed through the trace redaction, inside the existing steering cap. A log that cannot be read degrades to the current text plus one line saying so; never a block, never a raise, the heal budget charged as today. The worker skill's red-CI line says the log is in the brief and fetching it is not a step.

## Context

| File | Role |
|------|------|
| `devclaw/goal/remote_checks.py` | Gains `failed_job_log_tail(owner_repo, head_sha, check_name, lines)` — one bounded `gh run view --log-failed` (or the jobs API) under the rollup's wall-clock bound; returns `""` on any failure |
| `devclaw/goal/tick_guards.py` | `_ci_correction` renders the excerpt per failing check; `_autoheal_ci` reads it on the red path only |
| `devclaw/config.py` | `DEVCLAW_CI_LOG_TAIL_LINES` (default 120), one home |
| `runner/skills/_writes-code/50-repo-gate-conflict.md` | The `BLOCKED: env` line excludes CI-log access; the red-CI guidance says the excerpt is in the brief |
| `docs/reference/env-vars.md`, `docs/flows/task-execution.md` | The new var; the red-CI hop now carries the fact |

## Requirements

1. Per failing check named by the rollup, the correction carries a labelled excerpt (check name, job id, head sha, the last N lines of the failed step), in rollup order.
2. N is one config value; the excerpt is ANSI-stripped and passes the existing secret redaction before it enters steering; `_cap_steering` truncates the excerpt, never the instruction.
3. A read that fails or times out yields the current correction plus `log unavailable: <reason>`; nothing blocks, the heal budget is charged exactly as today.
4. Zero cognition; the read happens only on the red path of a heal window the tick already spends a `gh` call on.
5. The worker skill no longer permits reporting CI-log access as a missing capability.

## Plan

1. `failed_job_log_tail` in `remote_checks.py`, best-effort, bounded, never raises.
2. Thread it through `_ci_correction(branch, rc, excerpts)`; call it from the red branch of `_autoheal_ci`.
3. Config value + env-var doc row.
4. Skill line + flow doc.
5. The existing red-path class test is parametrized over "log read" / "log unavailable" (the "never blocks" tripwire); no sibling. No text-redaction helper existed in the repo — `scrub_log` masks GitHub token shapes and Authorization headers on top of Actions' own `***` masking.

## Rejected alternatives

- A read-only GitHub token in the sandbox: the fence carries no credential by design.
- The whole log as a file in the checkout: a write outside the materialize span.
- A per-ecosystem error filter: project-tooling knowledge devclaw must not hold (IX).

## Tasks

- [X] `failed_check_logs` + `DEVCLAW_CI_LOG_TAIL_LINES` (the tail is read inside `check_pr` on the red path, so the fake checker seam covers it — no second injection)
- [X] `_ci_correction` renders the excerpts, bounded to fit beside the instruction under the 4 000-char steering budget (plan-time fact: the char budget binds harder than 120 lines)
- [X] Skill + docs (env-vars row, flow doc step M, INDEX tags)
- [ ] Live proof: one red rollup on the box after deploy, the next brief carries the tail (owner reads the goal log)

## Done-When

A red rollup on the live instance produces a steering line carrying the failing job's tail; no `mechanical:env` hold names CI-log access afterwards; ruff/mypy/lint-imports/suite green.
