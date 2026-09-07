# TinySpec: a self-healing hold is a wait, not a terminal problem

**Branch**: fix/hold-is-not-terminal
**Date**: 2026-09-07
**Status**: done
**Complexity**: small

## What

The problems catalog counts every goal that ENTERS a blocked state as a
`terminal` occurrence, whatever the `blocked_kind`. A `mechanical:ci` hold
("waiting for CI — the done-gate opens when the checks settle") is therefore
recorded as a dead stop, survives two run-cycles trivially, and the
self-issue filer — whose only guard is `terminal_count > 0` — files it as a
recurring bug at severity `high` (#853). The hold is the brake working; the
filing is noise that costs the owner a triage.

## Context

`GoalStore.transition` records the block at the single BLOCKED-entry choke
point (`devclaw/goal/store/status.py`) with `recovered=False`. The tick's
`_autoheal_ci` / `_autoheal_prep` / `_autoheal_env_cap` lift those holds at
zero cognition; when a heal budget is spent, `_heal_give_up` parks the goal
with one ping — and records nothing, so the one occurrence that IS terminal
is the one the catalog never sees.

| File | Role |
|------|------|
| `devclaw/goal/models.py` | Adds `SELF_HEALING_BLOCK_KINDS` next to the `blocked_kind` taxonomy |
| `devclaw/goal/store/status.py` | Entry into a self-healing kind records `recovered=True` |
| `devclaw/goal/tick_guards.py` | `_heal_give_up` records the terminal occurrence under the parked kind (three call sites pass it) |
| `tests/test_problems_catalog.py` | The block-entry class test is parametrized over kinds; the give-up case is added (fail-closed / brake tripwire class) |
| `docs/architecture.md` | The problems-catalog paragraph states the rule |

## Requirements

1. A goal entering `mechanical:ci`, `mechanical:prep`, `mechanical:env` or
   `mechanical:corrupt_doc` bumps `recovered_count`, never `terminal_count`.
2. Every other kind (`needs_answer`, `bug`, `mechanical:lost_ref`,
   `mechanical:dispatch_cap`, `donegate_churn`) stays terminal on entry.
3. A heal give-up records ONE terminal occurrence under the parked kind,
   with the reason in the message.
4. `should_file` is untouched: a hold that always heals never files; a hold
   that gave up files after the recurrence bar, as before.
5. Zero cognition, zero new writes on the idle path (the record rides the
   existing block/give-up transitions).

## Plan

1. The kind set as a frozenset in `models.py` (the one home of the taxonomy).
2. `recovered=written.blocked_kind in SELF_HEALING_BLOCK_KINDS` at the choke point.
3. `_heal_give_up(..., kind=...)` records `category="block"` terminal; ci/prep/env sites pass their kind.
4. Parametrize the existing entry test; add the give-up test.

## Rejected alternatives

- **Skip recording holds entirely.** Loses the "how often does the brake
  fire" signal the self-heal rate (spec 039 US2) reads from `recovered_count`.
- **Fix the filer to skip `mechanical:*`.** The catalog would still lie
  (`terminal 4` for a wait); the filer is downstream of the fact.
- **Close #853 by hand.** An instance fix; the next CI wait files again.

## Tasks

- [X] `SELF_HEALING_BLOCK_KINDS` in `models.py`
- [X] Entry record uses it; give-up records terminal
- [X] Class test parametrized + give-up case
- [X] `docs/architecture.md` paragraph
- [X] Close #853 as catalog noise once merged

## Done-When

`test_block_transition_records_a_block_problem[mechanical:ci-1-0]` and
`test_heal_give_up_records_the_terminal_occurrence` pass; the full suite,
ruff, mypy, lint-imports are green; #853 is closed with the class named.
