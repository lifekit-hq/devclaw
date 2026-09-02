# TinySpec: a tripwire landing is a partial, not a failure — the next session continues from the branch

**Branch**: fix/partial-settlement-continuation
**Date**: 2026-09-02
**Status**: draft
**Complexity**: small

## What

The context tripwire (spec 021 US2) and the saga feed-forward (spec 012 US1)
contradict each other, and the contradiction costs a human every time a
spec-sized goal outruns its context window.

At 75% the runner tells the worker (`runner/runner.py:684`): *"Land a coherent
partial increment now: make tasks.md honest, commit the work with the specs/
artifacts... **the next session continues from the workspace**."* The worker
obeys and commits real artifacts onto `goal/<id>` — the branch the NEXT action
is placed on (`queue/settle.py:453`, "mirroring how the goal layer preps
`goal/<id>` before each action"). The landing is a success of the mechanism.

Then two things treat it as a failure:

1. `goal/tick_settle.py:248` — `productive = 1 if (poll.status == "done" ...)`.
   A landing isn't `done`, so it earns **no dispatch-cap refund**.
2. `goal/prior_increments.py:120` — a `status=failed` entry renders *"did NOT
   land — its work is not in the tree, and repeating that attempt unchanged
   will fail the same way."* Under `goal-branch` delivery that sentence is
   **false**: the artifacts are in the next worker's working tree.

So worker N+1 boots on a branch containing a complete `tasks.md`, is told the
tree is empty, re-plans from scratch, spends the window on speckit again,
tripwires again, lands again, settles failed again. Two rounds exhaust the
cap with zero delivered increments and the goal parks for the owner.

Live proof: `devclaw-030-env-admission-2026-09-01` — two dispatches, both
`BLOCKED: context budget exhausted — this session produced the complete
implementation plan and task artifacts but zero implementation code`, zero
PRs, `mechanical:dispatch_cap`, parked ~20h until steered by hand on
2026-09-02. `specs/030-env-admission/tasks.md` was on the goal branch the
whole time, complete and honest.

Class fix: a tripwire landing that materialized a non-empty span is its own
settlement outcome — `partial`. It refunds the cap (it is forward progress)
and feeds forward as *continue from the branch*, never as *nothing landed*.

## Context

| File | Role |
|------|------|
| `devclaw/queue/settle.py` | Will be modified — worker-blocked path (~:1350) persists the tripwire-landing signal + the materialized span alongside the failure; `pre_run_sha` is already in scope (~:995) |
| `devclaw/goal/models.py` | Will be modified — add `PollResult.landed_partial` beside `no_change` (~:402) |
| `devclaw/goal/engine.py` | Will be modified — add `_landed_partial(result_json)` parser, same shape as `_no_change` (~:338), wired in `_poll_task` (~:295) |
| `devclaw/goal/tick_settle.py` | Will be modified — `partial` settlement status + cap refund (~:238-248) |
| `devclaw/goal/prior_increments.py` | Will be modified — render `status=partial` as continue-from-the-branch; scope the "not in the tree" sentence to genuine failures |
| `devclaw/task_change.py` | Context — the ONE definition of the change span; the span is READ from it, never re-derived (CLAUDE.md invariant) |
| `runner/runner.py` | Context — `_LAND_BUDGET_PROMPT` + `_ContextTripwire.landed`; unchanged |
| `tests/test_task_retry.py` / `tests/test_goal_tick.py` | Will be modified — extend the existing dispatch-cap brake class test; do NOT mint a sibling |

## Requirements

1. On the worker-blocked path, when the runner result carries
   `tripwire.landed is True`, the task result persists that signal **and** the
   materialized `pre_run_sha..HEAD` span, obtained from `task_change` — never
   re-derived (CLAUDE.md: one definition of the change). The task still
   settles **`failed`**: fail-closed (#186) is untouched, nothing ships, no
   PR, no gate is bypassed. This changes what the settle *records*, not what
   it *allows*.
2. `PollResult.landed_partial: bool` is parsed defensively — anything other
   than a literal `True` reads `False`, so an engine that predates the field
   keeps today's behaviour byte-for-byte.
3. A settle with `landed_partial` **and a non-empty span** records settlement
   status `partial` and refunds the dispatch cap (`productive = 1`). The goal
   returns to `idle` and re-dispatches on the next tick instead of parking.
4. A `partial` does **NOT** reset `last_progress_at` / the no-progress
   watchdog. A landing that only re-planned is not a delivered increment, so
   the existing watchdog remains the brake against a goal that lands forever
   without shipping. No new counter, no schema change — the brake already
   exists.
5. A tripwire landing whose span is **empty** is not partial: it settles
   `failed` with no refund, exactly as today. Nothing landed, so there is
   nothing to continue from, and the cap must still catch it.
6. `prior_increments.render` gains a `status=partial` entry line stating that
   the increment's artifacts **are on the goal branch** and the session must
   continue from them rather than re-plan. The existing *"did NOT land — its
   work is not in the tree"* sentence applies to `failed` / gate-FAILED
   entries only — it must never render for a `partial`.
7. Only devclaw-generated facts still cross the feed-forward boundary (#358):
   the signal is the runner's structured `tripwire.landed` flag plus devclaw's
   own span, never the worker's `Agent summary:` prose.

## Plan

1. `queue/settle.py`: in the `_WORKER_BLOCKED_MARKER` branch (~:1350), before
   `mark_failed`, read the tripwire dict already in hand (~:1199) and, when
   `landed` is true, attach the span via the existing change primitive and
   persist a result payload on the task row alongside the failure text.
2. `goal/models.py` + `goal/engine.py`: add the field and its parser; wire
   into `_poll_task`. Mirror `_no_change` exactly.
3. `goal/tick_settle.py`: compute `partial` from `poll.landed_partial` + a
   non-empty span; use it for the `record_settlement` status and for
   `productive`. Leave `delivered` (and therefore the watchdog) alone.
4. `goal/prior_increments.py`: add the `partial` entry rendering and scope the
   failed-entry sentence.
5. Extend the dispatch-cap brake class test with the partial cases.

## Tasks

- [ ] Persist `tripwire.landed` + the materialized span on the worker-blocked
      settle path (`queue/settle.py`), task still `failed`
- [ ] `PollResult.landed_partial` + `_landed_partial` parser wired into
      `_poll_task` (`goal/models.py`, `goal/engine.py`)
- [ ] `partial` settlement status + cap refund in `goal/tick_settle.py`;
      `last_progress_at` deliberately untouched
- [ ] Continue-from-the-branch rendering for `status=partial`, and scope the
      "not in the tree" sentence to genuine failures
      (`goal/prior_increments.py`)
- [ ] Extend the dispatch-cap brake class test: a landed partial refunds the
      cap and re-dispatches; an empty-span landing does NOT refund and still
      parks; a partial does not reset the no-progress watchdog
- [ ] Extend the feed-forward test: a `partial` entry renders
      continue-from-the-branch and never the "not in the tree" sentence
- [ ] Full suite + `ruff check .` + `mypy` green
- [ ] Docs honesty: if this makes `docs/flows/task-execution.md` or spec 021's
      tripwire description wrong, fix it + its `docs/INDEX.md` currency tag in
      this PR

## Done When

- [ ] All tasks checked off
- [ ] A goal whose worker tripwire-lands twice in a row keeps dispatching
      instead of parking at `mechanical:dispatch_cap`, and each successor's
      brief tells it to continue from the committed artifacts
- [ ] A worker block with nothing committed still fails closed, unrefunded,
      and still parks at the cap — the brake is narrowed, not weakened
- [ ] Zero-token guard tests (`FakeClaude.calls == 0`) untouched and green
