# Research: Unattended-Week Operation (Phase 0)

Grounded 2026-08-29 by a full seam-mapping pass over the tree at a5d6555.
Format per decision: what was chosen / why / alternatives considered.

## D1 — Where the merge fires

**Decision**: inside `_resolve_done_gate` (`devclaw/goal/tick_donegate.py`),
after the CI-rewrite stage (:408-467) and BEFORE the `ACHIEVE` transition
(:474). The verdict is only final at :474 — the remote-CI check can rewrite
`achieved → off_track` before it, and merging on a verdict that then gets
rewritten would merge unclosed work.

**Rationale**: `Event.ACHIEVE` is legal only from `EXECUTING_IDLE`/`BLOCKED`
(`transitions.py:106/130`); the goal sits in `EXECUTING_IDLE` here. The
close's post-transition tail (:482-533) is best-effort by contract ("never
undo a verified close") — putting the merge there would make
unmerged-but-done a legal state, violating FR-002.

**Alternatives considered**: (a) merge in the close tail — rejected above;
(b) merge in `tick_settle`'s conflicting-PR probe — rejected: that path runs
on every settle, not at close, and carries an explicit do-not-reintroduce
banner (:407-419) for mid-flight merges (#486).

## D2 — Merge mechanics

**Decision**: new module `devclaw/goal/merge_on_close.py` shelling
`gh pr merge --squash` (same `_run_gh` conventions as `mergeability.py`,
which stays read-only per its tombstone). Outcomes: MERGED /
ALREADY_MERGED (success, FR-004) / CONFLICT / CLOSED_UNMERGED (hard) /
ERROR (hard after bounded retries). Branch delete is tolerated-fail.

**Alternatives**: extending `mergeability.py` — rejected, its module
docstring is a deliberate tombstone documenting #641; keeping it read-only
preserves that historical record's integrity.

## D3 — Conflict self-heal shape

**Decision** *(revised at implement time)*: machine-steering injection — on
CONFLICT the close returns the goal to `idle` (RESUME_IDLE) with an
`auto-conflict` steering row; the NEXT tick's advance dispatches the
resolution increment through the wholly-normal pipeline. On an *idle* goal
auto-* steering counts as work (the ralph-loop behavior the churn-brake
tinyspec pins), so this needs zero new plumbing. Bounded by the persisted
`merge_heal_attempted` flag; re-confirmation of `achieved` comes free via
the normal deliver → done-gate cycle. This is exactly the
`_apply_corrections` shape the done-gate already uses.

**Alternatives**: (a) direct `_dispatch_action` from the close — rejected at
implement time: `_resolve_done_gate` carries no engine/prepare_ws handles
and importing `tick_dispatch` into `tick_donegate` risks an import cycle;
one heartbeat of extra latency is irrelevant at week scale. (b) a
side-channel ops agent — rejected (operator concurred): the normal pipeline
already carries the sandbox, verify gate, and change-span accounting a
resolution needs. The blocked-goal caveat that originally argued against
steering does not apply: the conflict path lands on an IDLE goal, where
machine steering is deliberately work.

## D4 — Resume retries the merge, not the gate

**Decision**: persist `pending_merge_pr` on `GoalStatus`; an early branch in
`_handle_long_lived_advance` (before brief construction) re-attempts the
merge when set — zero cognition. New blocked kind `mechanical:merge_failed`.

**Alternatives**: re-entering the done-gate on resume — rejected: burns a
cognition round to re-derive a verdict that already stands (FR-003), and
done-gate churn accounting would count it.

## D5 — Lane skip-over

**Decision**: change the project-lane predicate (`devclaw/goal/
project_hold.py`) so `phase="blocked"` does not occupy the lane.

**Rationale**: the 2026-08-28 night proved blocked-holds-lane idles a repo
(022-one-lane-b sat queued behind blocked 728 all night). Fix the class at
the predicate.

**Alternatives**: per-goal skip flags — rejected (instance fix, not class).

## D6 — Self-deploy trigger path

**Decision**: devclaw's own container cannot rebuild/restart itself (no
Python self-deploy path exists; spec 005 is script + `workflow_dispatch`
Actions on a self-hosted runner). So: devclaw records `deploy_pending` in
`meta`, and a zero-token tick check triggers `gh workflow run deploy.yml`
once `store.count_running() == 0` (running-only — `has_active_work()` also
counts pending, which would deadlock quiescence against the queue).
Auto-rollback lives in a new `deploy/deploy-devclaw-auto.sh` wrapper on the
runner side: capture current SHA from `/health`, deploy new, on health-gate
failure redeploy prior SHA exactly once.

**Rationale**: recreating `devclaw-mcp` SIGKILLs in-flight sandboxes (spec
005 edge case) — hence the quiescence gate; `queue.recover()` +
`reset_running_to_pending()` remain the boot safety net.

**Alternatives**: (a) devclaw shelling `docker compose` against itself —
rejected: the process dies mid-command, no supervisor owns the rollback;
(b) frozen-week (no self-deploy) — operator explicitly chose self-deploy.

## D7 — Quiet-mode choke point

**Decision**: wrap the `Notifier` object at its binding
(`service.py:132-134`) with a `QuietNotifier` decorator implementing the
same Protocol; add `send_critical` for the instance-dead class, called by
the auth-pause ping site (`tick.py:887-909`) and the rollback-failure path.

**Rationale**: `_notify` (`tick_context.py:118`) is NOT a single choke
point — the cycle report sends directly on `self._notifier`
(`service.py:510`). Wrapping the object catches both call sites with zero
changes at ping sites.

**Alternatives**: routing the cycle report through `_notify` — rejected:
touches idempotency accounting (`sent_at=None` semantics) for no gain;
text-pattern filtering inside the notifier — rejected: fragile class
detection by emoji prefix.

## D8 — Persistence shapes

**Decision**: quiet mode = one `meta` key via `ControlPlaneMixin`
(the `operator_hold` exemplar: absence == off, corrupt JSON degrades to
off, reader returns a tuple). Deploy intent/outcome = `meta` keys.
Suppressed pings = a new `suppressed_pings` table (ordered, unbounded reads
are bounded by LIMIT). `GoalStatus` gains `pending_merge_pr`,
`merge_heal_attempted`. Every state-shape change ships its doctor check
(spec 016 FR-014).

## D9 — What is explicitly NOT reused

- `devclaw/delivery/deploy.py` — that is the per-goal PREVIEW deploy
  surface (`devclaw-deploy-<slug>` containers), a different system from
  instance self-deploy. Not touched.
- `loom/merge_queue.py` — fan-out lane ordering, unrelated to PR merging
  (and scheduled for demolition by spec 022 US3).
- The `triaged_notify` interceptor — propose-only triage for a different
  purpose (`TRIAGE_ELIGIBLE`); quiet mode is orthogonal and simpler.
