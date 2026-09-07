# Tiny spec — a pointer goal whose contract closed asks, instead of grinding

## What

When a pointer goal's referenced issues are ALL closed and the done-gate has
already refused, stop re-dispatching a worker. Raise a typed Problem and block.

## Context

`fs-431-hygiene-sentinels` (created 2026-08-31) has logged this pair every
round for a week:

> referenced issue #431 is closed — dropped from the remaining scope
> (dispatch-boundary freshness guard)
> all referenced issues are closed but done-gate previously refused
> (2 round(s)) — dispatching worker to complete the remaining contract

Those two lines contradict each other. The first says the issue is out of
scope; the second says go finish the contract that came from it. `fs-421` is in
the same loop with #421 (5 consecutive rounds visible in its log).

The mechanism: a pointer goal reads its `done_when` LIVE from the referenced
issues (spec 019). Once every issue is closed there is no contract source left
that any dispatch can amend — but the done-gate keeps judging against a pinned
revision of the contract as it was. So the loop re-dispatches a worker against
an unamendable contract, forever. `fs-431` has burned 8 rounds and 7
increments this way and is still `off_track`.

The current comment justifies the dispatch as *"an issue can be closed by a
partial implementation (e.g. a PR with `Closes #N` on an intermediate
increment while the full spec remains unbuilt)."* That case is real — and it is
already served by the branch ABOVE, which proposes done on the first pass
(`donegate_rounds == 0`) and lets the grounded gate decide. What this spec
changes is only what happens AFTER the gate has refused at least once: at that
point the loop has evidence that the contract is unmet AND evidence that its
source is gone, and no further dispatch can reconcile them. That is precisely
the shape spec 031 defines a Problem for.

The freshness guard and the done-gate are each correct alone; the deadlock is
that neither owns the case where they disagree. This spec gives it an owner.

**Rejected alternative — treat "all issues closed" as achieved.** Closing a
ticket is not evidence the code works, and it would let a human's housekeeping
merge a goal branch the gate has actively refused. The owner may CHOOSE that
via the `accept_close` option; the loop must not assume it.

## Requirements

- **R1** All referenced issues closed AND `donegate_rounds > 0` AND no merge
  heal owed ⇒ raise a Problem, BLOCK, ping; no worker dispatch.
- **R2** The Problem names the gate's own last refusal as the clause, so the
  owner sees WHY the gate refuses, not just that it does.
- **R3** Options are the existing vocabulary: `ACCEPT_CLOSE` (default — the
  owner closed the ticket, so the presumption is "done"), `CORRECT` (re-open
  or amend the issue, then continue), `CANCEL`. Because `ACCEPT_CLOSE` carries
  `closes_goal=True`, an elapsed timebox under `strict` PARKS rather than
  closing — the existing Q2 → C rule, unchanged.
- **R4** UNCHANGED: the first pass (`donegate_rounds == 0`) still proposes done
  and lets the gate decide, and an owed merge-conflict increment still
  dispatches (spec 025 FR-017).

## Plan

1. `goal/tick.py` — replace the `donegate_rounds > 0` dispatch with the
   Problem+BLOCK, mirroring the revoked-readiness site directly above it.
2. Tests — extend the pointer-lane class test with the deadlock case and a
   guard that the first-pass and merge-heal paths still dispatch.
3. Docs — CLAUDE.md's pointer-goal paragraph + INDEX currency.

## Tasks

- [x] T1 the Problem at the closed-contract branch (R1, R2, R3)
- [x] T2 tests: deadlock raises, first-pass and merge-heal still dispatch (R4)
- [x] T3 docs honesty + INDEX currency

## Done when

- A pointer goal with all issues closed and a refusing gate blocks with a
  Problem instead of dispatching.
- The first-pass propose-done and the merge-heal increment are untouched.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
