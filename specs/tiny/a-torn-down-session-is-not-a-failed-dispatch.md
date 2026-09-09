# TinySpec: a torn-down session is devclaw's failure, not the goal's dispatch

**Branch**: `fix/torn-down-session-is-not-a-failed-dispatch`
**Date**: 2026-09-09
**Status**: IMPLEMENTED 2026-09-09 — north-star verdict was SHRINK; **Denys ruled
implement in full** and the ruling stands (the judge proposes, the owner decides).
**Complexity**: small

## North-star case

- **Failure moved**: stopped when it shouldn't. A wall-clock teardown costs the goal one of its two dispatch slots, so two timeouts park a healthy goal on `mechanical:dispatch_cap` and wait for the owner.
- **Number that shows it**: teardown-caused cap trips — machine issue #882, 4 terminal
  occurrences, 0 recovered. (The draft first cited `mechanical:dispatch_cap` idle at
  29,205s / 68% of devclaw-caused idle; that is the PARENT class. Of the seven cap trips
  on 2026-09-08/09 exactly one was a teardown — the rest were review-requested-changes ×2,
  a broken `verify_cmd` ×2 and `change_class` gate-input edits ×2. Corrected at the
  north-star pass so the spec is not measured against a number it does not move.)
- **Cut when**: timeouts stop appearing in the problems catalog for a month. Then the accounting fix is guarding nothing and the budget rule can go back to counting every failure alike.

## north-star verdict: SHRINK to R3 (the wall-clock fact) — R1 admitted only if a residue survives

Judged 2026-09-09 against live `get_scorecard_metrics` (336h) and `get_loop_health` (36h).

```
axis: stopped when it shouldn't (+ ran but needed the owner)
number: dispatch_cap is 5 of 12 owner `resume` verbs in 14d; teardown-caused trips = 4 lifetime (#882), ~1 of 7 recent
cut when: no teardown appears in the problems catalog for a month
owner after: FEWER — removes one `continue`/`resume` per teardown, adds none
domain: state (dispatch-budget accounting) · order: FACT (R3) before brake-accounting (R1) — correct
class: a system's own resource limit firing is charged to the work it interrupted
boundary: tick_settle.py `productive` refund — ONE mechanism there; this extends it, does not add a third
weight: 0 new kinds, 0 env vars, 0 tables; reversible in one PR
SE: pass (layer 2, rides the existing ACTION_SETTLED write, no second writer)
AI-eng: pass (R3 supplies a fact; R1 is an invariant in Python; no tick-path cognition)
bullshit test: next week we'd see fs-431-shaped goals re-dispatch after a timeout — roughly one
               fewer owner verb per fortnight. Real, but ~1/7 of the number the draft cites.
```

**Owner's ruling (2026-09-09): implement in full.** Reasons 1 and 2 below stand as
recorded — the number was corrected in the North-star case above, and R1 was built with the
bound reason 2 asked for — but the scope was not cut. Kept here as direction memory: if the
teardown residue turns out to be empty once R3 is live, R1 is the part to remove.

**Verdict reasons, strongest first:**

1. **The cited number belongs to the parent class, not to this change.** The draft headlines
   `mechanical:dispatch_cap` idle at 29,205s / 68% of devclaw-caused idle. That figure is the whole
   cap class. Teardowns are a minority of it: of the seven cap trips in the 09-08/09-09 run, one was
   a teardown; the others were code-review-requested-changes (×2, issue-493), a broken `verify_cmd`
   (×2, scanner-broad-universe: `dotnet test` from a directory with no solution), and `change_class`
   gate-input edits (×2, fs-557). Shipping this moves ~15% of the quoted number. The North-star case
   must cite teardown-caused trips (4 lifetime, #882), or it is the metric-on-paper failure of test 1.

2. **R1 is partly guarding a case that already refunds.** `poll.landed_partial` already refunds the
   cap for a torn-down action that committed a coherent partial increment. The uncovered residue is
   only teardowns that committed *nothing* — and R3 is precisely the change that converts those into
   landed partials. Constitution IX orders fact before brake: ship R3, re-measure, and admit R1 only
   if teardowns with an empty span survive it. R1's "bounded to once per goal per delivered increment"
   adds a special case to the settle hot path; it should be paid for by evidence, not anticipation.

3. **Everything else passes, and the second-job test passes strongly.** It removes an owner verb
   the 2026-09-07 ruling says should never have existed (`resume` is not a decision), adds none,
   invents no kind, and extends the single mechanism already at that seam instead of adding a third.
   That is why this is SHRINK and not CUT.

**Not this spec's problem, but larger than it:** the other 6-of-7 cap trips are three distinct
classes — a goal that cannot self-correct on review feedback, a project whose `verify_cmd` does not
match its workspace, and a worker that reaches for gate inputs when blocked. Each is worth more than
the teardown fix.

## Root cause

`devclaw/config.py:143` sets `TASK_TIMEOUT_S` to 3600. When it fires, the sandbox is torn down with no terminal result and the action settles `failed`.

`devclaw/goal/tick_settle.py` refunds the dispatch cap only for work that delivered; failures and gate-FAILED work consume a slot. A wall-clock teardown is neither. It is devclaw ending the session, and the goal is charged for it.

CLAUDE.md already states the intended rule: *"the dispatch budget measures failed dispatches, not progress; a transient (a gate crash, an idle timeout) earns one refund."* That sentence is in the Problem's own `why` text, offered to the owner as a reason to press `continue`. So the owner is asked to hand-apply, one Problem at a time, a refund the design already says is owed.

fs-431 is the worked example. Its two dispatches on 2026-09-08 were a 3600s teardown at 19:09 and a `change_class` failure at 20:16. The cap tripped at 20:32 and has been waiting for Denys since.

There is a second, quieter half. The worker is never told its wall-clock budget, so it cannot choose to land a smaller slice before the axe. It plans as if time were unbounded and loses everything at 3600s, including the commit it had not made yet.

## Requirements

1. A wall-clock teardown does not consume a dispatch slot. It is devclaw's own limit firing, in the same class as a gate crash, and the existing "transient earns one refund" rule covers it. Apply it mechanically rather than through an owner's `continue`.
2. The refund is bounded. Repeated teardowns on one goal with nothing delivered are not transient; the second one behaves exactly as the current cap does and raises the Problem. This must not become an unbounded retry.
3. The worker is told its remaining wall-clock in the brief, as a fact, so it can size a slice and commit before the limit rather than after it.
4. No new kind, no new env var, no new table. The refund rides the existing `ACTION_SETTLED` write and the existing transient concept.

## Rejected alternatives

- **Raise `DEVCLAW_TASK_TIMEOUT_S`.** The error text itself suggests this and it is wrong. It moves the wall without telling anyone where the wall is, and a longer session on one account quota is the 2026-08-29 six-task loss.
- **Auto-retry a timed-out task.** A retry replays the same task plus its failure history, which is the reasoning already written into the prompt-overflow path. Same shape, same answer: not auto-retried.
- **A new `mechanical:timeout` kind.** A fifth kind at a boundary that already carries four, against the standing "replace, never add a third" rule. The existing cap accounting is the right seam.

## Plan

1. Classify the wall-clock teardown at settle and refund the slot, bounded to once per goal per delivered increment.
2. Pass the remaining wall-clock into the dispatch brief.
3. Extend the existing dispatch-cap class test with the teardown case. Do not mint a sibling test.

## Tasks

- [x] Teardown classified and refunded at settle, bounded — `PollResult.torn_down` derived
      through the EXISTING `derive_failure_class` (`timeout` bucket), refunded in
      `goal/tick_settle.py`, bounded by `goal_status.teardown_refunds` (reset by the same
      productive settle that clears every other damping counter)
- [x] Remaining wall-clock in the brief — `goal/tick._wall_clock_fact()`, omitted entirely
      when `DEVCLAW_TASK_TIMEOUT_S <= 0` so an unbounded deployment renders byte-identically
- [x] Extend the cap class tests (parametrized, no siblings minted) —
      `test_unshipped_refund_leaves_the_watchdog_armed` (renamed from the landed-partial-only
      name; both shapes now share one invariant) and `test_unproductive_settle_keeps_dispatch_count`
      (the BOUND: a second teardown still burns its dispatch). Both drive the teardown case from
      `queue.settle.wall_clock_teardown_msg()` through `goal.engine._torn_down` rather than a
      hardcoded string, so a wording drift in the queue fails the test instead of silently
      stopping refunds
- [x] Doctor check for the new persisted column (spec 016 FR-014) —
      `instance.schema.goal_status` diffs the live table against the shape the code declares
      (built from the real bootstrap, not a restated list), with a seeded-fault test
- [ ] Close machine issue #882 (on merge)

## Done-When

A goal whose only failure was a wall-clock teardown dispatches again without an owner verb. A second teardown with nothing delivered still raises the Problem. `mechanical:dispatch_cap` idle falls below its 14-day figure of 29,205s. Suite, ruff, mypy and lint-imports green.
