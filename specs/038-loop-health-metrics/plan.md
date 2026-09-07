# Implementation Plan: Loop Health Metrics

**Branch**: `feat/038-loop-health-metrics` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/038-loop-health-metrics/spec.md`

## Summary

Make the three ways the north star can fail measurable without a single new
model call: (US1) every heartbeat tick attributes the interval since the
previous tick to ONE cause, run-length-stored, bucketed at read time into
devclaw-caused / owner's turn / no work available, headlined by a not-stuck
rate; (US2) the problems catalog's existing recovered/terminal counters roll
up into a self-heal rate; (US3) a permanent `usage_ledger` row is written at
the two places usage already enters the system — the task settle (per
attempt) and the cognition trace — and backfilled from whatever transcripts
survive; the worker half of that ledger gets its FIRST real source (the
agent's own session transcript, read by the runner at the end of the run —
live check 2026-09-07: zero settled tasks carry usage today); (US4) the
naive `tokens_per_merged_pr` is REPLACED by durable, shape-segmented cost per
outcome; (US5) the console overview shows the five numbers and the usage page
a monthly trend; (US6) the grader's assessed unit count is persisted at grade
time and joined onto the existing per-goal convergence row at close, with a
calibration read that refuses to report below a stated sample.

Every write is a pure SQLite write inside an existing single writer
(StateStore / GoalStore); every read is a projection; the runner change is
one bounded best-effort reader behind the claude-specific branch of the ACP
seam.

## Technical Context

**Language/Version**: Python 3.11 (host), stdlib-only Python in the sandbox runner, TypeScript/React (console SPA, Vite)

**Primary Dependencies**: FastMCP/Starlette routes (existing), sqlite3 (existing), no new packages

**Storage**: the shared `devclaw.db` — new tables `loop_spans`, `usage_ledger`, `intake_grades` (StateStore-owned) + six nullable columns on `goal_convergence` (GoalStore-owned); all idempotent `CREATE TABLE IF NOT EXISTS` / swallowed `ALTER`, the repo's migration pattern

**Testing**: pytest, fully stubbed; ONLY tripwire-class tests ship (FR-019 doctor seeded fault, FR-020 absent-is-never-zero parametrized across layers, one zero-token guard case for the attribution write, the fake-agent seam proof staying green)

**Target Platform**: Linux server (VPS container) + per-task docker sandbox

**Project Type**: modular monolith service + static SPA

**Performance Goals**: zero cognition calls on every path this feature adds (Constitution III); the tick write is one SELECT + one INSERT/UPDATE; all reads are bounded SQL over indexed columns

**Constraints**: FR-004a exactly one attribution write point (the heartbeat); FR-010 transcript retention untouched; FR-011/FR-020 absent ≠ zero at every layer; FR-015a replace, never sit alongside; FR-026 nothing actuates on a prediction; SC-006 permanent rows stay under 1% of DB growth

**Scale/Scope**: ~100 ticks/day → ≤100 `loop_spans` rows/day worst case (run-length coalesced in practice to a handful); one `usage_ledger` row per attempt + per cognition call (~50–300/day at ~120 bytes); one `intake_grades` row per graded issue

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

One line per principle in `.specify/memory/constitution.md`; answer each, do not delete any.

- [x] **I. OAuth only** — no spawn site changes. The runner reads a transcript file the agent already wrote; no key, no env change. The sandbox gains one writable tmpfs subpath under the curated config dir (`projects/`), which holds transcripts, never credentials (the auth files stay RO binds).
- [x] **II. Model-agnostic worker layer** — the transcript reader lives in the runner behind `_is_claude_adapter(acp_command)`; a non-claude ACP agent takes the existing path (ACP-reported usage or absent). The fake-agent regressions keep proving the seam (`"usage" not in result` for the fake stays green). No skill text changes.
- [x] **III. Zero-token idle** — the attribution write is a pure SQLite write appended AFTER `tick_all`'s existing pass (below every early return for pause/hold); the backfill is a one-shot watermarked SQL scan on the same cheap slot as the prunes. A `FakeClaude.calls == 0` case covers the attribution path.
- [x] **IV. Single writer** — `loop_spans`, `usage_ledger`, `intake_grades` are written only by StateStore methods; `goal_convergence` columns only by `GoalStore.record_convergence`; the tick reaches the store through the existing engine getattr seam; no view is read back; task rows are never touched (usage rows are a sibling table keyed by task_id).
- [x] **V. Fail-closed verification / done is a proposal** — untouched; this feature reads outcomes, it never gates one (FR-026 forbids acting on the prediction).
- [x] **VI. Loud failure** — every derived figure carries the count it is based on; an empty sample reads `null`, never 0; a heartbeat gap longer than three ticks is recorded as an explicit `unobserved` span, never attributed; the doctor check names a silent worker-usage source.
- [x] **VII. Fix the class** — the class is "measurement that expires or was never persisted": one permanent ledger fed at the two existing entry points, one attribution sampler, one calibration join — not a per-metric patch.
- [x] **VIII. Cognitive guardrail?** — none added or kept. Every mechanism here is measurement; nothing classifies, gates or scaffolds the model. US6 records an estimate that already exists and stops.
- [x] **IX. Instruct thin, verify thick** — domains, by module: `loop_spans` / `usage_ledger` / `goal_convergence` columns = **state**; cost per outcome and the usage ledger = **money**; the runner's `usage` block and the sandbox tmpfs = **protocol** (what comes out of the worker) plus one **fact** supplied to the agent's own tooling (a writable transcript path). The console is surfacing over those. No project-code knowledge enters devclaw; the transcript format is Claude Code's own standard artifact (the same one `ccusage` reads) — adopted, not invented.

## Project Structure

### Documentation (this feature)

```text
specs/038-loop-health-metrics/
├── plan.md              # This file
├── research.md          # Phase 0: live findings + decisions D1–D9
├── data-model.md        # Phase 1: the three tables + the convergence columns
├── quickstart.md        # Phase 1: validation scenarios
├── contracts/
│   ├── loop-health.md   # /loop-health.json, /calibration.json, /usage.json history, MCP get_loop_health
│   └── runner-usage.md  # the runner result `usage` block amendment (transcript source)
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
devclaw/
├── state_store/
│   ├── schema.py            # CREATE loop_spans, usage_ledger, intake_grades
│   ├── health.py            # NEW: LoopHealthMixin — record_loop_sample, record_task_usage,
│   │                        #      record_cognition_usage, record_intake_grade, backfill, reads
│   ├── core.py              # StateStore composes the mixin
│   ├── observability.py     # append_trace_event → cognition usage row (same commit)
│   └── evals.py             # (unchanged)
├── loop_health.py           # NEW (leaf, pure): cause vocabulary, bucket_for, derive_loop_cause,
│                            #      rate math — no store access, unit-testable
├── telemetry.py             # compute_loop_health, compute_cost_per_outcome, compute_usage_history,
│                            #      compute_calibration; scorecard usage block REPLACED (FR-015a)
├── goal/
│   ├── tick.py              # tick_all → _tick_all_pass + the ONE attribution write
│   ├── engine.py            # GoalEngine.record_loop_sample / backfill_usage_ledger / unarmed_ready_issues seams
│   ├── state.py             # ALTER goal_convergence ADD COLUMN ×6
│   ├── state_status.py      # record_convergence writes the new columns
│   └── store/status.py      # GoalStore.record_convergence computes predictions/dispatches/steered/cost
├── queue/settle.py          # per-attempt record_task_usage at the two runner call sites
├── intake.py                # grade_and_label(record=...) seam; regrade/grade_backlog/recover thread it
├── server/
│   ├── routes/observability.py   # /loop-health.json, /calibration.json, /usage.json gains `history`
│   ├── tools/observability.py    # get_loop_health MCP tool (+ re-export in tools/__init__.py)
│   └── tools/intake.py           # passes store.record_intake_grade
├── doctor/checks_instance.py     # check_loop_health_tables (FR-019)
├── engine/sandcastle.py          # --tmpfs <claude dir>/projects (transcript scratch)
runner/runner.py                  # _claude_transcript_usage fallback when ACP reported none
console/src/
├── api.ts                        # fetchLoopHealth, fetchCalibration, InstanceUsage.history
├── pages/Overview.tsx            # loop-health strip (five metrics, unknown ≠ zero)
└── pages/Usage.tsx               # monthly trend over the ledger
tests/
├── test_loop_health_absent_is_never_zero.py   # FR-020 tripwire, parametrized across layers
├── test_goal_tick.py                          # + tick_all attribution is zero-token
├── test_doctor.py                             # + seeded fault for the new tables
└── test_runner_acp.py                         # existing fake-agent seam proof stays green
docs/
├── architecture.md, runbooks/doctor.md, INDEX.md   # honesty updates
└── specs/021-worker-context-budget/contracts/runner-result.md  # usage block source field
```

**Structure Decision**: extend the existing modular-monolith homes. One new leaf module (`devclaw/loop_health.py`, pure) so the cause vocabulary and bucket rule have exactly one definition (FR-005a) importable by the tick, the telemetry reads and the tests without a store; one new StateStore mixin (`state_store/health.py`) following the `evals.py`/`problems.py` pattern so every new table has one writer.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| (none) | | |

## Slicing (the unit of review)

| PR | Stories | Non-test surface |
|---|---|---|
| A | US1 + US2 | `loop_health.py`, `state_store/health.py` (spans + reads), `tick.py` write point, engine seam, `/loop-health.json` + MCP tool, doctor check, scorecard untouched |
| B | US3 + US4 | `usage_ledger` (settle + trace writers, backfill), runner transcript source + sandcastle tmpfs, `compute_cost_per_outcome`, scorecard usage block replaced, `/usage.json` history |
| C | US6 | `intake_grades` + grade seam, `goal_convergence` columns, `compute_calibration`, `/calibration.json`, `no_goal_armed` emission |
| D | US5 | console overview strip + usage trend; API client |

Stacked in that order; each PR body names its base and the #235 retarget rule.
