# Delivery flows — how work becomes merged code

How a goal's dispatches turn into PRs, how those PRs reach `main`, and where
the dispatch-cap backstop sits.

**devclaw never merges.** Every PR it opens is landed by a human. Auto-merge,
the per-action delivery topology it keyed off, and the program PR-stack
reconciler were all deleted in #641 — see "Why nothing merges" below.

## The shape: one goal = one shared branch = one cumulative PR

Every goal has `lifecycle="executing"` — i.e. every goal created since the
spec-008 shrink — and `goal/delivery_strategy.py` returns the same answer for
all of them. The worker plans in-sandbox (speckit `specs/*/` artifacts in the
repo); the shared goal branch is the delivery surface.

```
        every dispatch checks out the SHARED
        branch  goal/<goal-id>   (not main)
                        |
                        v
        advance k commits STACK on advance k-1
        all pushes go to the SAME PR
                        |
                        v
        the PR stays OPEN for the whole goal
        (merging it would delete the shared
         branch and fork the next advance
         back to main — the 2026-08-08 amnesia)
                        |
                        v
        done-gate = the single review moment
        for the one cumulative PR
                        |
                        v
        the HUMAN merges
```

Increments never overlap by construction, so there is nothing to merge
mid-goal. A settled delivery gets one read-only advisory: a `gh pr view` asking
whether the PR has gone CONFLICTING with its base
(`goal/mergeability.py:pr_conflicting`). A CONFLICTING verdict logs and pages
the owner — the next increment would otherwise stack onto a branch that can no
longer land. An unknown verdict says nothing; it never reads as "all clear".

## Programs (removed by spec 022 US3)

The program/DAG dispatch lane — and the dormant `[P]` fan-out that was its
last producer — was demolished by spec 022 US3. Nothing creates program rows
anymore; `ref_kind="program"` survives only on legacy persisted refs (polling
one now blocks the goal loudly), and the `programs` table stays readable via
`get_program`/`list_programs` as history. Delivery has exactly one shape: one
increment at a time on the goal branch, one push, one cumulative PR.

## Why nothing merges (#641)

Auto-merge fired only for a **per-action** delivery, and nothing has selected
per-action since the spec 008 shrink stamped `executing` on every goal at
creation. It had been unreachable in production for months, hidden by tests
that reached it by seeding a goal shape production had stopped writing.

The program PR-stack reconciler went with it. It existed to shepherd a stack of
per-action PRs to main and close the superseded ones; goal-branch delivery
never makes a stack. What was left was a hazard
rather than a capability: a reconcile that merged the cumulative
goal-branch PR would have re-created the amnesia
bug through a path that bypassed the goal-branch skip.

The through-line: in companion mode a human reviews and merges every PR.
Machinery that merges without one is compensating for an absent reviewer.

## The dispatch cap (runaway backstop)

`cap = len(backlog) + 2` (it no longer widens on a checklist — the checklist
is gone with the host planning chain). Progress-aware since #172/#173:

```
   dispatch            -> counter +1
   settle SUCCESSFUL   -> counter -1   (done; gate passed OR gateless —
                                        reviews, programs, no-gate tasks)
   settle FAILED       -> stays        (failed run, or gate FAILED)

   counter >= cap      -> goal BLOCKED, owner notified
   owner steer/resume  -> unblocks, counter reset to 0
```

Both `steer_goal` and `resume_goal` clear the counter; the cap block is
human-gated by design — unlike `mechanical:prep`, it never auto-heals.

Only a goal looping on **broken** dispatches accumulates to the cap. A
healthy goal — including one that grounds every delivery in a read-only
verification review — never blocks. Churn on successful-but-aimless work is
caught by the done-gate's direction evaluation after each settled advance
(plus its churn brake) and the 6h no-progress watchdog, not by this counter.

## Field history that shaped this

- 2026-06-26 `finance-sentry-mcp-v3/v4` — PR fan-out / shared-branch deletion
  → the never-merge-mid-goal rule and the stacked goal branch.
- 2026-07-05 `closeloop-bench` — planner claimed "PR merged" for an unmerged
  PR → settle detail now states the PR's real state, built after the merge
  attempt.
- 2026-07-07 `closeloop-mission-v2` blocked at cap 6 with all work merged
  → #172 refund for gated deliveries.
- 2026-07-09 `closeloop-mission-v2` blocked again on its own on_track
  verification reviews; five zombie superseded PRs found open on closeloop
  → #173 refund-all-successful-settles + the program reconcile step (itself
  deleted in #641 once goal-branch delivery made PR stacks impossible).
