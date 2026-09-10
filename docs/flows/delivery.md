# Delivery flows — how work becomes merged code

How a goal's dispatches turn into PRs, how those PRs reach `main`, and where
the dispatch-cap backstop sits.

**Nothing merges mid-flight; the confirmed-achieved close merges.** A goal's
cumulative PR stays open for the goal's entire life (#486). When the done-gate
confirms `achieved`, the close squash-merges that PR into the default branch
(spec 025, `goal/merge_on_close.py`) — the one seam where devclaw merges. A
close that cannot merge does not happen: the goal parks
`mechanical:merge_failed` after one bounded, pipeline-dispatched conflict
self-heal, and the parked goal releases its project lane to the queued
successor. The merge also requires the PR's CI rollup to be green on the same
head the done-gate opened on (spec 032).

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
        confirmed achieved → the close
        squash-merges the PR  (spec 025)
        cannot merge → mechanical:merge_failed
```

Increments never overlap by construction, so there is nothing to merge
mid-goal. A settled delivery gets one read-only advisory: a `gh pr view` asking
whether the PR has gone CONFLICTING with its base
(`goal/mergeability.py:pr_conflicting`). A CONFLICTING verdict logs and
grounds the next brief (`pr_state … mergeable=CONFLICTING`); it no longer
pages the owner (spec 045 FR-009) — the close routes the conflict to the
bounded resolution increment itself (below), so the ping only ever asked for
a hand rebase the loop now does. An unknown verdict says nothing; it never
reads as "all clear".

## Merge-on-close (spec 025)

`goal/tick_donegate.py` runs the merge between the evaluator's `achieved`
verdict and the `ACHIEVE` transition — nothing on the settle path merges, and
`mergeability.py` stays read-only. The sequence, all mechanical (`gh`
subprocesses, zero cognition):

1. **Same-green-head check** (`_ci_hold_before_merge`, spec 032 US1). The
   PR's CI rollup is re-read right before merging. It must be green (or the
   goal has no PR), and the head must equal `ci_green_head` — the head the
   done-gate opened on. Otherwise the goal blocks `mechanical:ci` with
   `pending_done_proposal=True`: a moved head re-opens the done-gate on the
   new head; a pending rollup waits, zero-token. The same read carries the
   PR's mergeability (spec 045): a CONFLICTING PR skips the hold — GitHub
   creates no merge ref for it, so its checks can never report — and goes
   straight to step 3, at every seam that reads CI before a close (the gate
   open, the accepted close, this hold, the CI auto-heal).
2. **`attempt_merge`** (`merge_on_close.py`) finds the open PR for
   `goal/<id>` and runs `gh pr merge --squash`. Outcomes: `merged`;
   `already_merged` (an operator merged by hand — success); `no_pr` (a
   no-change goal — the close proceeds); `conflict`; `closed_unmerged` (a
   human rejected the PR — closing as achieved would discard the work); or
   `error` (forge/network/branch protection).
3. **One bounded conflict self-heal** (FR-017). On the first `conflict` the
   goal returns to `idle` with `merge_heal_attempted=True` and a machine
   steering row (`source="auto-conflict"`); the next tick's advance dispatches
   the resolution increment through the normal pipeline — verify gate and a
   fresh done-gate round included — and the close re-attempts the merge. A
   second conflict is not healed. The increment merges the default branch,
   and the judged span leaves out what the base already carries (spec 045
   US1) — before that, main's own workflow bumps failed the increment's
   `change_class` gate and the heal could never land. A human `resume_goal`
   refunds the heal like every other mechanical budget (spec 045 FR-007).
4. **Failure posture.** Any non-success outcome blocks the goal
   `mechanical:merge_failed` with `pending_merge_pr` set and an owner ping.
   `resume_goal` re-attempts the MERGE only (step 1 included), never the
   done-gate — the achieved verdict stands (FR-003). A blocked goal is not a
   lane holder (`goal/project_hold.py`, skip-over): the queued successor
   starts instead of waiting.
5. **After a merge** the workspace is fast-forwarded to the default branch
   (best-effort; the next goal's `prepare_ws` is the guarantee), a
   devclaw-repo merge records a pending self-deploy (spec 025 US2), and the
   goal transitions to `done`.

`done_when` is the sole pre-merge authority; there is no additional pre-merge
review, and human review moves post-merge (FR-006).

## Programs (removed by spec 022 US3)

The program/DAG dispatch lane — and the dormant `[P]` fan-out that was its
last producer — was demolished by spec 022 US3. Nothing creates program rows
anymore; `ref_kind="program"` survives only on legacy persisted refs (polling
one now blocks the goal loudly). The lane's read-only remnants
(`get_program`/`list_programs`, the program SSE route, the store's program
CRUD) were pruned once nothing could write a row — a pre-retirement
`programs` table may survive on an old instance as unread history. Delivery
has exactly one shape: one
increment at a time on the goal branch, one push, one cumulative PR.

## History

#641 deleted auto-merge (reachable only by the per-action delivery topology
nothing had selected since the 008 shrink) and the program PR-stack
reconciler; `merge.py` became the read-only `mergeability.py`. Spec 025
(2026-08-29) reversed that doctrine at exactly one seam — the
confirmed-achieved close — and nowhere else.

## The dispatch cap (runaway backstop)

`cap = len(backlog) + 2` (it no longer widens on a checklist — the checklist
is gone with the host planning chain). Progress-aware since #172/#173:

```
   dispatch            -> counter +1
   settle SUCCESSFUL   -> counter -1   (done; gate passed OR gateless —
                                        reviews, no-gate tasks)
   settle FAILED       -> stays        (failed run, or gate FAILED)

   counter >= cap      -> goal BLOCKED, owner notified
   owner steer/resume  -> unblocks, counter reset to 0
```

Both `steer_goal` and `resume_goal` clear the counter; the cap block is
human-gated by design — unlike `mechanical:prep`, it never auto-heals.

Since spec 031 (2026-09-02) every human-gated block — a done-gate
`needs_human`, the churn park, a worker honest-block, the dispatch-time park —
raises a **typed Problem** through `devclaw/goal/problems.py` in the same
transaction as the block (what, clause, why, options, default, timebox), and
the owner resolves it with one of exactly two verbs, `correct_implementation`
or `decide` (`POST /goals/{id}/resolve`), each recording a Decision and
restoring the budgets exactly as a steer does. A worker honest-block raises
its Problem on the settle that carries it — it no longer spends two more
dispatches to reach the cap. `steer_goal` is refused while a Problem is open.

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
