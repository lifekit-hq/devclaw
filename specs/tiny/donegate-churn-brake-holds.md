# TinySpec: donegate churn brake holds — machine steering never unblocks a parked goal

**Branch**: fix/donegate-churn-brake-holds
**Date**: 2026-08-25
**Status**: done

> Bookkeeping reconciled 2026-08-29: shipped in #685 (commit 1de089f) but the
> file was never flipped from draft — verified: `has_unread_human_steering`
> (store/content.py), the tick.py:637 gate, and all three named tests exist.
**Complexity**: small

## What

The done-gate churn brake parks a thrashing goal for the owner
(`blocked_kind="donegate_churn"`, tick_donegate.py:484) — then defeats itself:
the same branch appends the gate's corrections as steering
(`_apply_corrections` → `append_steering(source="auto-eval")`), and the
blocked-unblock gate in `_handle_long_lived_advance` (tick.py:550) counts ANY
unread steering as work. The brake writes its own key into the lock.
Live proof: `devclaw-pr-authorship-2026-08-24` parked at rounds 3, 4, and 5 on
2026-08-25 and self-resumed within one tick each time, burning three extra
worker+review+eval rounds while the owner slept. Class fix: machine-appended
steering (`source` prefixed `auto-`) never unblocks a blocked goal; only human
steering or an explicit `resume_goal`/`steer_goal` does.

## Context

| File | Role |
|------|------|
| `devclaw/goal/tick.py` | Will be modified — blocked branch of the should_plan gate (~:550) requires human steering |
| `devclaw/goal/store/content.py` | Will be modified — add `has_unread_human_steering(goal_id)` (source NOT LIKE 'auto-%') |
| `devclaw/goal/tick_donegate.py` | Context — churn branch parks + applies corrections; unchanged |
| `devclaw/goal/tick_context.py` | Context — `_apply_corrections` appends with `source="auto-eval"`; unchanged |
| `tests/test_goal_tick.py` | Will be modified — named regression tests beside the existing churn tests (~:2502) |

## Requirements

1. A goal in `phase="blocked"` whose only unread steering rows have a
   machine source (`source LIKE 'auto-%'`, today only `auto-eval`) stays
   blocked on tick: no dispatch, no cognition call (`FakeClaude.calls == 0`),
   steering rows stay unread.
2. A blocked goal still unblocks on human steering (any non-`auto-*` source,
   e.g. `denys`), and `resume_goal` still unblocks it with no steering at all.
3. Idle-path behavior is byte-unchanged: an idle goal with unread `auto-eval`
   corrections still dispatches next tick — the below-cap done-gate
   ralph-loop (tick_donegate.py:498) depends on this.
4. Machine rows parked with the goal are NOT consumed while blocked; the
   first dispatch after a human unblock consumes them into the brief, so the
   gate's corrections still reach the next session.
5. The filter is by source prefix (`auto-`), not the literal `auto-eval` —
   any future machine-appended steering inherits the rule (fix the class).

## Plan

1. `store/content.py`: add `has_unread_human_steering(goal_id) -> bool` —
   one SELECT over `goal_steering` with `consumed_at IS NULL AND source NOT
   LIKE 'auto-%'` (goes through `state_content` like its siblings).
2. `tick.py` `_handle_long_lived_advance`: in the gate, replace the blocked
   branch's `should_plan = work` with
   `should_plan = bool(finished_detail) or store.has_unread_human_steering(goal_id)`;
   the idle branch and the consume-on-dispatch path (all row ids) unchanged.
3. Tests in `tests/test_goal_tick.py` next to the existing churn coverage.

## Tasks

- [x] Add `has_unread_human_steering` to the store content mixin (+ state layer query)
- [x] Gate the blocked branch of `_handle_long_lived_advance` on it
- [x] `test_donegate_churn_park_survives_auto_eval_steering` — parked goal +
      unread auto-eval rows → still blocked, zero cognition, rows unread
- [x] `test_blocked_goal_unblocks_on_human_steering_and_consumes_parked_corrections` —
      human row added → dispatches; brief carries BOTH rows; both consumed
- [x] `test_idle_goal_still_advances_on_auto_eval_corrections` — ralph-loop intact
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off; named regression tests pass
- [x] A goal parked by the churn brake stays parked across ticks until a human acts
- [x] Existing zero-token guard tests (`FakeClaude.calls == 0`) untouched and green
