# Tiny spec — the problems catalog reads recent-first, and retired kinds are purged

## North-star case

- **Failure moved**: ran but needed the owner. The owner reads the problems
  catalog every morning and mentally discards the dead half before the live
  signal is visible. On 2026-09-09 the default `list_problems` read returned
  285 rows; the top five were led by `cognition/goal_planner` (n=60, last seen
  46 days earlier) and `cognition/trend-detector` (n=21), both raised by code
  that no longer exists.
- **Number that shows it**: rows in the default read that can still recur —
  285 returned / 120 seen inside 14 days before; after, 120 returned and zero
  naming a deleted cognition role.
- **Cut when**: the catalog stops accumulating dead vocabulary — no retired-role
  rows and no read surface defaulting to all-time. Then there is nothing to
  hide and nothing to purge.

## What

Two changes to the read surface of the `problems` catalog:

1. **One window default, both surfaces.** The MCP `list_problems` tool gains
   `since_days` (default 14) and passes it as the `since_ms` the store already
   accepts. The console route, which already windows at a hard-coded 30 days,
   reads the same shared default. `since_days=0` returns all-time.
2. **Retired kinds are purged at boot.** A migration deletes `problems` rows
   whose `(category, kind)` names a cognition role that no longer exists in the
   code, plus a doctor check that fails if any survive.

## Context

The catalog is bounded per fingerprint (`category | kind | normalize(message)`)
but unbounded in vocabulary: 285 distinct rows have accumulated, 165 of them
last seen more than 14 days ago. `count` is a LIFETIME counter and the sort is
`ORDER BY count DESC, last_seen_ms DESC`, so a row that recurred 60 times two
months ago outranks one that recurred 13 times yesterday. The catalog feeds the
self-issue filer and the owner's morning class-pick; both degrade when the
default read is majority-dead.

The store already solved half of this — `list_problems(since_ms=...)` exists
and the console route uses it. The MCP tool simply never plumbed it, so the two
surfaces disagree about what "the problems catalog" means. This spec does not
add a mechanism; it finishes wiring one and gives its default a single home.

The other half a window cannot fix. Three cognition roles were deleted —
`goal_planner` (the host-cognition chain, 008 shrink), `summary` and
`trend-detector` (spec 037, 2026-09-06) — and `cognition/<role>` rows are
keyed on `payload["role"]` at `loom/trace.py:614`. The live roles are exactly
`evaluator`, `review`, `intake_readiness`, `reachability`. Rows for the three
dead roles can never be raised again, yet two of them were last seen 10 days
ago and so sit INSIDE any reasonable window. Absence of recurrence is evidence
only when the raise site still exists; deletion of the raise site is proof.

**Rejected: a `dormant` lifecycle state gated on execution volume.** The first
design derived a fourth lifecycle value from "quiet for N days AND ≥N settled
tasks of exposure since". It answers "is this fixed?", which is a question
nobody is asking — the owner wants the old rows out of the way, which a window
already does with no new state and no threshold to tune. It also could not be
computed for over half the catalog: only 30 of 285 rows carry `last_goal_id`,
so per-project exposure is unavailable where it would matter most.

**Rejected: purging by staleness.** Deleting rows because they are old destroys
the cycle report's history (`cycle_report.py:242` reads all-time on purpose)
and would delete live-but-seasonal problems. Only provably-unraisable rows are
deleted; everything else is hidden, not lost.

## Requirements

- **R1** `list_problems` (MCP) accepts `since_days: int = 14`; `0` disables the
  window. It passes `since_ms` to the store; no new query logic.
- **R2** The window default lives in ONE place, `state_store/problems.py`, and
  both the MCP tool and the console route read it. Neither hard-codes a number.
- **R3** The tool docstring states the default window and how to defeat it, so
  a caller cannot mistake a windowed read for the whole catalog.
- **R4** A boot migration deletes `problems` rows whose category is `cognition`
  and whose kind is in a declared `RETIRED_COGNITION_ROLES` set. Idempotent: a
  second boot matches nothing.
- **R5** `RETIRED_COGNITION_ROLES` is declared next to the live-role vocabulary
  it complements, with the spec that retired each role named in a comment.
- **R6** A doctor check (`instance.legacy.retired_cognition_problems`) FAILS
  when any retired-role row survives, naming the count and the remedy.
- **R7** The cycle report's all-time read (`cycle_report.py:242`) is unchanged.

## Plan

`state_store/problems.py` gains `DEFAULT_PROBLEM_WINDOW_DAYS = 14` and
`RETIRED_COGNITION_ROLES`. `schema.py` gains one `DELETE` beside the existing
forward-compat migrations. `server/tools/observability.py` gains the parameter;
`server/routes/observability.py` drops its local `_THIRTY_DAYS_MS` for the
shared constant. `doctor/checks_instance.py` gains the check and registers it.

## Tasks

- [ ] T1 `DEFAULT_PROBLEM_WINDOW_DAYS` + `RETIRED_COGNITION_ROLES` in `state_store/problems.py`
- [ ] T2 purge migration in `state_store/schema.py`
- [ ] T3 `since_days` on the MCP tool + docstring (R1/R3)
- [ ] T4 console route reads the shared default (R2)
- [ ] T5 doctor check + registration (R6)
- [ ] T6 doctor seeded-fault test (tripwire class: doctor seeded-faults)

## Done-When

- `list_problems()` with no arguments returns only rows seen in the last 14
  days; `list_problems(since_days=0)` returns all-time.
- No `problems` row exists for `goal_planner`, `summary` or `trend-detector`
  after one boot, and a re-inserted one is deleted by the next boot.
- `doctor` reports `instance.legacy.retired_cognition_problems` ok, and FAILS
  on a seeded retired-role row.
- One default: `grep -rn "30 \* 24 \* 60 \* 60 \* 1000" devclaw/server/` is empty.
