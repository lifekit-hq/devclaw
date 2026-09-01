# Console goal legibility — the empty-page cluster

## What

Four console gaps that together rendered the 2026-09-01 morning as "0 goals
need you / every goal page empty" while 12 goals were parked and one wedged
goal held the finance-sentry lane all night. One PR: make the console tell
the operator what the store already knows.

## Context

Observed live 2026-09-01 (Denys, morning after the 08-31 run):

- lifekit-dashboard said 12 goals need attention; the console Overview said
  "All clear". Three *different* "needs you" definitions existed:
  `control.py` node.json (blocked OR stalled OR needs_human/stalled verdict),
  `console/src/status.ts` `needsYou()` (blocked OR needs_human — dead code),
  and `Overview.tsx` (phase === "blocked" only — the one actually rendered).
- 11 of the 12 goals had zero tasks because they were queued behind the
  wedged lane-holder — but `goal_json` doesn't carry `queued_behind`/
  `queued_reason`, so their pages were blank with no explanation.
- The one goal WITH history (fs-479, 13 tasks) folded all of it behind a
  collapsed "Completed milestones" disclosure — the page read as empty.
- The Plan tab rendered `specs/041-liquidity-brain/tasks.md` (another goal's
  spec) on every finance-sentry goal page: `_read_plan` picks the
  newest spec dir at the ref, and a shared project workspace makes that
  whichever goal planned last.

## Requirements

- ONE definition of "needs you", one home, computed server-side; every
  surface (node.json count, goal rows, Overview page) renders it.
- A queued goal's detail page states why it is empty and links the
  lane-holder.
- A goal whose only tasks are settled history shows that history unfolded.
- The Plan tab shows only a spec the goal's own branch introduced
  (branch-minus-default attribution); no spec ⇒ honest "no plan yet",
  never another goal's plan. Default-branch resolution failure degrades
  the subtraction to a no-op (best-effort collector convention).

## Plan / Tasks

- [x] `_projections._goal_needs_you(g)` — the one predicate (handles both
  direction shapes); `needsYou` field on `_goal_row`; `control.py` count
  delegates to it.
- [x] `goal_json` += `queuedBehind`/`queuedReason` (already on
  `goals.get_goal`).
- [x] Frontend: `GoalRow.needsYou` + Overview renders it (stat + section);
  delete the dead `status.ts` predicate; queued-lane banner + tasks
  empty-label in `GoalDetail`; `MilestoneTasks` unfolds settled milestones
  when nothing is active.
- [x] `_read_plan`: candidates = tasks.md at `origin/goal/<id>` (then local
  branch) minus tasks.md at `origin/HEAD|main|master`; drop the HEAD and
  worktree fallbacks (they are the cross-goal bleed).

## Done-When

- Overview "Needs you" equals node.json `goals.needsYou` for the same store
  (same predicate, one home).
- A queued goal page states the lane reason instead of a bare "Tasks 0".
- fs-479-shaped goal (all tasks settled) shows its task history without a
  click.
- A finance-sentry goal that authored no spec shows "no plan yet", not
  liquidity-brain's checklist.
- Suite, ruff, mypy green; `npm run build` (tsc) green. No new tests —
  ordinary console behavior, not a tripwire class (tests-to-tripwires rule).
