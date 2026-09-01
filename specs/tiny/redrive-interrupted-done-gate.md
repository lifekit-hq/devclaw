# Re-drive an interrupted done-gate close (issue #784)

## What

A close resolution that dies between the done-check verdict and the
evaluator's verdict must not strand the goal for a full re-plan cadence.

## Context

fs-479, 2026-08-31: done-check review returned "done" at 09:12:35; the
account-wide quota pause hit at 09:12:59 and killed the done-gate evaluator
mid-flight. The goal was left `idle` with the close still owed and
`last_plan_at` freshly stamped, so the next planning opportunity was the
1-day cadence away — 24 hours of a wedged project lane (7 queued goals)
from a 24-second-unlucky pause. The pause machinery preserves task WIP,
but a mid-close interruption was not re-driven on resume.

## Requirements

- After a done-check review settles, an evaluator death of ANY kind (quota
  pause, OOM, crash, restart) leaves the goal in a state the next
  non-paused tick acts on immediately — never `cadence` later.
- Zero-token idle guard holds: recovery keys off persisted state read by
  the existing cheap gates; no new LLM call, no new idle-tick work.
- No new persisted state shape (no schema change, no doctor check needed).

## Plan / Tasks

- [x] `tick_settle._resolve_polling_done_gate`: the `DONE_GATE_SETTLED`
  transition clears `last_plan_at` (the established resume idiom —
  `cadence_due()` reads None as due). A completed resolution supersedes it:
  `achieved` closes the goal, a refusal steers work in, and the next
  dispatch stamps a fresh `last_plan_at` either way.
- [x] Named tripwire test (pause-and-resume brake class):
  `test_interrupted_done_gate_close_reopens_cadence_next_tick` — evaluator
  aborts mid-close ⇒ `last_plan_at is None` and the next tick is not IDLE.
  Verified to FAIL against the unfixed code.

## Done-When

- The test above pins the invariant; full suite, ruff, mypy green.
- A repeat of the fs-479 sequence re-proposes done on the first tick after
  the pause clears.
