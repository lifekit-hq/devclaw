# TinySpec: one definition of a goal's next move — the lane and the tick read the same one

**Issue**: none (Denys, 2026-09-08 evening — "I don't want to fix symptoms; fix the root cause, whether it's lifecycle or whatever")
**Branch**: fix/one-definition-of-runnable
**Date**: 2026-09-08
**Status**: proposed — Denys reviews before implementation (tinyspec lane gate)
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. On 2026-09-08 the owner's `accept_close` on fs-318 / fs-421 / fs-429 was recorded at 13:44–17:53 UTC, spec 041 (a Decision executes on the next tick) was live from 17:52, and at 21:00 all three still read `idle · next: decide: accept_close`. The finance-sentry lane was busy the whole evening (fs-431's two failed tasks, then fs-554's done-check), and the three closes — which touch no checkout — sat behind it. Of the four goal PRs shipped in the previous 24 h, three were merged by hand for the same reason.
- **Number that shows it**: `get_scorecard_metrics(24h).interventions` on 2026-09-08 20:51 UTC — 4 steers, all "accept_close was already decided — proceed"; `per_achieved_goal` 16.0. After: a recorded owner `accept_close` closes on the next tick whether or not another goal holds the project, and `interventions.items` carries no "already decided" steer.
- **Cut when**: the lane is retired (one goal per project, nothing to serialize) — then there is one consumer of "runnable" and nothing to keep in agreement.

## Root cause

"What can this goal do next" was defined **three times** and they drifted:

1. `project_hold.holder_map` — who holds the lane: in flight, or a held done proposal, or (not merge-owing and) unread steering or a due cadence. Written 2026-09-01 (runnable-head rule).
2. `tick._handle_long_lived_advance` — whether the tick plans: a settle, unread steering, a pending Decision (added by spec 041 FR-001), the owner's standing `accept_close` (FR-003), or a due cadence.
3. The **position** of each lane-free finalize relative to the hold gate in the same function: the merge retry and the ci-settled re-drive sit above it "by design" (their comments say so); spec 041's pending-decision `accept_close` was placed below it.

Spec 041 updated definition 2 and not 1 or 3. Two consequences, one observed and one latent:

- **Observed**: a goal whose only move is the owner's `accept_close` is not a lane candidate (definition 1 is right — a close needs no lane), yet the tick queues it behind the holder (definition 3), so a decided close waits for the whole lane to drain. With three runnable successors queued, it can wait all night.
- **Latent (single-writer hole)**: a goal whose only move is a dispatching Decision (`correct`, `continue`, `correct_implementation`) IS work for the tick but NOT a candidate for the lane. Two such goals on one project — the shape of a morning decision batch — both find no holder in the same sweep and both dispatch: the #553/#722 two-writers class spec 010 closed by construction, reopened by spec 041.

The class: the lane's candidacy rule and the tick's work rule are the same fact ("this goal's next move") stated in two places, and the third statement is an ordering nobody can check. Constitution IV (single writer) is only as strong as the agreement between the two.

## What

ONE function, `project_hold.next_move(goal, status, store, *, settled=False)`, answers the question, and both consumers read it:

| Move | Meaning | Holder candidate | Waits for the lane | Tick plans |
|---|---|---|---|---|
| `in_flight` | work already running | yes (outranks age) | — | (settle path) |
| `heal` | a `mechanical:ci` hold owing a done proposal; the heal re-drives the gate | yes | — | no (the heal acts) |
| `lane` | a dispatch into the checkout: a settle to retry, unread steering, a pending Decision, a held done proposal, a due cadence | yes | yes | yes |
| `lane_free` | a close on mechanical facts: a merge retry, the owner's standing `accept_close` | no | **no** | yes |
| `none` | nothing to do | no | (queued as today) | no |

The hold gate in the tick becomes "another goal holds AND my move waits for the lane"; `should_plan` becomes "my move is `lane` or `lane_free`". The order of the finalizers no longer decides anything — a lane-free move passes the gate wherever it sits.

Rejected: moving the accept_close finalize above the gate (the instance fix — leaves three definitions and the latent hole); a stored "needs lane" flag on the status row (a second writer to a derived fact, the #FR-005 mistake).

## Context

| File | Role |
|------|------|
| `devclaw/goal/project_hold.py` | Modified — `next_move` + the move constants; `holder_map` reduces to "candidates are the holding moves, in-flight first, then age" |
| `devclaw/goal/tick.py` | Modified — the hold gate and `should_plan` in `_handle_long_lived_advance` read `next_move`; the inline definitions are gone |
| `tests/test_goal_tick.py` | Extended — lane class test: a head whose only move is a dispatching Decision holds the lane (the successor is QUEUED) |
| `tests/test_merge_on_close.py` | Extended — spec 041 US1 class test: the owner's `accept_close` closes while another goal holds the lane, zero cognition |
| `docs/architecture.md`, `docs/INDEX.md` | The runnable-head paragraph names the one predicate |

## Requirements

1. `holder_map` and the tick's hold gate derive "needs the lane" from the same function; no second inline definition exists in `tick.py`.
2. A goal whose next move is `lane_free` is never `QUEUED`.
3. A goal whose next move is `lane` — including a pending dispatching Decision — is a holder candidate.
4. A queued tick still spends zero cognition; the reads `next_move` performs are the ones the sweep's derivation already performs for the same goal.
5. Every existing lane test (spec 010 / 025 / runnable-head) passes unchanged.

## Plan

Add the predicate to the module that owns the lane; make both consumers call it; pin the two cases that were wrong.

## Tasks

- [ ] `next_move` + constants in `project_hold.py`; `holder_map` on top of it
- [ ] `tick.py` gate + `should_plan` on `next_move`
- [ ] Test: pending dispatching Decision ⇒ head holds, successor QUEUED
- [ ] Test: owner accept_close closes behind an in-flight holder, `FakeClaude.calls == 0`
- [ ] `docs/architecture.md` paragraph + INDEX tag
- [ ] Full suite + `ruff check .` + `mypy` + `lint-imports` green

## Done when

- On the VPS, an owner `decide accept_close` on a goal whose project lane is held closes on the next tick (the log carries "accept_close stands … closing without a gate round" while the holder is still in flight).
- No "already decided" steer appears in `interventions.items` over the following week.
