# Implementation Plan: Unit of Work & Planned Parallelism — P1

**Branch**: `feat/010-single-writer-project-lock` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Slice**: P1 only (FR-001…FR-009). P3 (`[P]` fan-out, FR-101…FR-105) stays
named-unsized per the spec and the constitution's slice-don't-estimate rule.

## Summary

At most one goal per project may dispatch increments; the rest queue and start
automatically when the holder goes terminal. This makes the #553 class
(independent plans colliding on one repo) structurally impossible rather than
mitigated.

Per the 2026-08-22 owner ruling (spec FR-005, amended), the hold is **derived,
not stored**: the holder of a project is the first non-terminal goal on it by
age, tie-broken on goal id. There is no lock row, no acquire, no release — so
there is no stale-hold class and no heal machinery. The waiting reason is
derived the same way at read time, so a queued goal costs zero writes per tick
on top of zero cognition.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none new — stdlib only

**Storage**: NO schema change. Project identity comes from `Goal.project_id`
(falling back to `workspace_dir`); terminality from `goal_status.phase`; age
from `MIN(goal_log.created_at)` — `create_goal` writes a "goal created" row, so
that timestamp is the creation moment.

**Testing**: pytest, fully stubbed. New `tests/test_project_hold.py` for the
derivation; tick-path behavior extends `tests/test_goal_tick.py`.

**Target Platform**: Linux host; layers 1–2 only

**Project Type**: single Python package

**Performance Goals**: the holder map is computed ONCE per heartbeat sweep
(N goal.yaml reads + one grouped SQLite query) and threaded into each
`tick_goal`; a queued goal's tick performs no further work.

**Constraints**: zero cognition and zero writes on a queued tick; cross-project
throughput unchanged.

**Scale/Scope**: 1 new module, ~4 touched files, ~10 named tests. ONE PR.

## Key design decisions

1. **Derived holder** (FR-005 as amended). `holder_map(store)` returns
   `{scope_key: goal_id}`. Pure function of rows already under the CAS'd
   transition discipline — no new writer (constitution IV).

2. **Ordering**: age ascending, tie-broken on goal id. Goals carry no priority
   field today, so FR-003's "priority band" clause is inert; the tie-break
   keeps the holder deterministic rather than arrival-dependent.

3. **Gate placement**: inside `_handle_long_lived_advance`, AFTER the
   settled-ok done-gate branch and BEFORE the steering read. This is the single
   dispatch choke point:
   - a fresh queued goal reaches it with zero cognition spent and returns
     `Outcome.QUEUED`;
   - a queued goal that still has in-flight work settles it first (nothing
     orphaned — the spec's upgrade edge case) and then dispatches nothing;
   - the steering read is *below* the gate, so a queued goal never triggers the
     lazy inbox ingest (which writes).

4. **Derived waiting reason**: `get_goal` computes "queued behind `<holder>`"
   at read time. `next` is deliberately excluded from the column-only status
   update path (it is state, not telemetry), and storing a second copy of a
   derived fact is the very thing the FR-005 amendment rejects.

5. **Scope key**: `project_id` when set, else the normalized `workspace_dir`.
   A goal with neither (self-fix goals with no registered project) is never
   queued — it has no project to contend for.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I. OAuth only** | PASS — unaffected | No spawn site, no cognition caller touched. |
| **II. Model-agnostic worker** | PASS — unaffected | Layer 2 only; the worker never learns about the hold. |
| **III. Zero-token idle** | PASS — actively guarded | The gate is a cheap local read placed before any cognition; a queued tick spends no LLM call and no write. Named test asserts `FakeClaude.calls == 0` on an all-queued sweep. |
| **IV. Single writer to state** | PASS — strengthened | The amendment means NO new writer and no second source of truth; the hold derives from rows the CAS'd transitions already govern. |
| **V. Verification fails closed** | PASS — unaffected | No gate added, removed, or re-consulted. Queuing delays dispatch; it never approves anything. |
| **VI. Loud over silent** | PASS | A queued goal's wait names its holder on its own status surface (no log-diving, SC-006); the upgrade case is visible rather than a silent interruption. |
| **VII. Fix the class** | PASS | #553 closed by removing its precondition. The FR-005 amendment is itself an instance: kill the stale-lock class rather than build machinery to survive it. |

**Gate result**: PASS. Complexity Tracking empty.

**Spec boundary**: 012 owns prompt content; this slice touches none of it.

## Project Structure

```text
devclaw/
├── goal/
│   ├── project_hold.py     # NEW — scope_key, holder_map, waiting_reason
│   ├── state.py            # + goal_created_at_ms_map() (one grouped query)
│   ├── tick.py             # holder map threaded through; the dispatch gate
│   ├── tick_context.py     # + Outcome.QUEUED; holders on TickContext
│   └── service.py          # get_goal derives the queued waiting reason
└── server/tools.py         # FR-009 — direct-dispatch warning

tests/
├── test_project_hold.py    # NEW — the derivation
└── test_goal_tick.py       # + queue/handover/zero-cost regressions

docs/
├── architecture.md         # FR-007 — canonical terminology + the hold
└── INDEX.md                # currency tags
```

**Structure Decision**: Layer 2 (GoalService + heartbeat), plus one layer-1
touch for FR-009's dispatch warning. No engine, delivery, gate, or worker
change — the Unit of Work is unchanged.

## Complexity Tracking

*No constitution violations — intentionally empty.*
