# Implementation Plan: Unattended-Week Operation

**Branch**: `025-unattended-operation` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/025-unattended-operation/spec.md`

## Summary

Give devclaw the one missing piece of an end-to-end unattended loop: when the
done-gate confirms `achieved`, squash-merge the goal's cumulative PR into the
default branch (with one bounded, pipeline-dispatched conflict-resolution
self-heal), then — for the devclaw repo only — trigger a quiescence-gated,
probe-checked, auto-rollback self-deploy; and add an instance-wide quiet mode
that suppresses-and-records every owner ping except instance-dead events.
Additionally, a parked goal must stop holding its project lane (skip-over).

Approach in one line per story:

- **US1**: a new mechanical merge step wired into `_resolve_done_gate`
  *before* the `ACHIEVE` transition — the goal closes only merged; a conflict
  dispatches one resolution increment through the normal advance pipeline; a
  hard failure parks `mechanical:merge_failed` with the achieved verdict
  persisted so resume retries the merge, not the done-gate.
- **US2**: after a devclaw-repo merge, wait for task quiescence, then trigger
  the existing spec-005 deploy workflow (`gh workflow run deploy.yml`) whose
  script gains an auto-rollback wrapper; devclaw records trigger + outcome.
- **US3**: a `QuietNotifier` decorator around the one real choke point — the
  `Notifier` binding in `GoalService.__init__` — with meta-table state, a
  suppressed-ping record, an MCP operator verb, and a `send_critical` path
  for the instance-dead class.

## Technical Context

**Language/Version**: Python 3.11 (asyncio), bash for deploy scripts

**Primary Dependencies**: existing devclaw internals only — no new external
deps. `gh` CLI (already the delivery credential path), docker compose (spec
005), GitHub Actions self-hosted runner (deploy.yml).

**Storage**: SQLite (`devclaw.db`) — goal store + state_store `meta` table
(ControlPlaneMixin shape); one new `suppressed_pings` table.

**Testing**: pytest, fully stubbed (FakeClaude / FakeEngine /
RecordingNotifier / seed_goal in `tests/goal_fakes.py`); named regression
test per behavior change; zero-token guards must stay green.

**Target Platform**: the VPS instance (docker compose, spec 005); dev = host
engine / stub.

**Project Type**: internal harness change across layers 2 (goal), 4 (queue
surface read-only), plus `deploy/` scripts and one MCP tool (layer 1).

**Performance Goals**: merge step adds ≤ one `gh` round-trip to a close;
quiet-mode check is one meta read per send; zero cognition added anywhere.

**Constraints**: `Event.ACHIEVE` legal only from `EXECUTING_IDLE`/`BLOCKED`
(transitions.py LEGAL); close-path steps after the transition are best-effort
("never undo a verified close") — which is exactly why the merge goes BEFORE
the transition; cycle-report send bypasses `_notify` (service.py:510), so the
quiet filter wraps the Notifier object, not `_notify`.

**Scale/Scope**: ~6 production modules touched, 1 new module, 2 script
files, ~15 named tests. Three independently shippable PRs (one per story,
US1 carries the lane skip-over).

## Constitution Check

*GATE: evaluated against `.specify/memory/constitution.md` v2.4.0.*

- **I (OAuth only)**: PASS — no cognition path added; `gh` is the delivery
  credential pillar, explicitly separate (delivery/__init__.py:16-18).
- **II (model-agnostic worker)**: PASS — the conflict-resolution increment is
  an ordinary `implement_feature` dispatch with a brief; no new worker
  wiring, no new skill home.
- **III (zero-token idle)**: PASS — merge/deploy/quiet are all mechanical
  (subprocess + SQLite); nothing runs on idle ticks. The conflict self-heal
  consumes normal advance-cycle cognition that would run anyway.
- **IV (single writer)**: PASS — merge state lives on `GoalStatus` via
  `GoalStore.transition`/`save_status`; suppressed pings + quiet state go
  through state_store mixins; no view is read back.
- **V (fail-closed; done is a proposal)**: PASS with amendment — the
  done-gate remains the sole close authority; merge is a consequence of its
  verdict, and a merge that cannot complete blocks the close (fail closed,
  loud). **This spec amends Principle V's trust rationale ("the human
  reviews every PR") in the same arc** — pre-declared in the spec's
  Constitution Impact section; the amendment ships in the US1 PR.
- **VI (loud failure)**: PASS — every new failure mode parks with a
  structured kind or records durably; nothing degrades silently.
- **VII (fix the class)**: PASS — lane skip-over is fixed at the lane
  predicate, not per-goal; the quiet filter wraps the Notifier class-wide.

**Violation requiring justification**: reversal of the #641 merge doctrine —
ruled explicitly by the operator (spec Clarifications); recorded in
Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/025-unattended-operation/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0 — seam map + decisions
├── data-model.md        # Phase 1 — new state shapes
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── operator-surface.md   # MCP verb + meta keys + blocked kinds
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
devclaw/
├── goal/
│   ├── merge_on_close.py        # NEW — squash-merge step + outcome types
│   ├── tick_donegate.py         # MODIFIED — merge step before ACHIEVE (:474)
│   ├── tick.py                  # MODIFIED — pending-merge resume path;
│   │                            #   conflict-heal brief plumbing
│   ├── tick_dispatch.py         # (context — dispatch chokepoint, unchanged API)
│   ├── project_hold.py          # MODIFIED — blocked goals release the lane
│   ├── notify.py                # MODIFIED — QuietNotifier decorator + critical path
│   ├── service.py               # MODIFIED — notifier binding wrap; deploy trigger
│   └── store/ + models.py       # MODIFIED — GoalStatus merge fields
├── state_store/
│   ├── control.py               # MODIFIED — quiet_mode meta verbs
│   └── schema.py + core.py      # MODIFIED — suppressed_pings table
├── server/tools/control.py      # MODIFIED — set_quiet_mode MCP verb
├── doctor/checks_instance.py    # MODIFIED — state-shape doctor check (FR spec 016)
deploy/
├── deploy-devclaw.sh            # MODIFIED — rollback-aware exit path
└── deploy-devclaw-auto.sh       # NEW — probe + auto-rollback wrapper
.github/workflows/deploy.yml     # MODIFIED — auto lane calls the wrapper
tests/
├── test_merge_on_close.py       # NEW
├── test_quiet_mode.py           # NEW
├── test_goal_tick.py            # MODIFIED — close-path + heal + skip-over cases
└── test_deploy_compose.py       # MODIFIED — wrapper drift checks
```

**Structure Decision**: three story-scoped PRs off `025-unattended-operation`
work: US1 (merge-on-close + conflict heal + lane skip-over + constitution
amendment), US2 (self-deploy trigger + wrapper), US3 (quiet mode). Each lands
independently; US2 and US3 have no dependency on each other but both build on
US1's close-path refactor landing first.

## Design — per user story

### US1: merge-on-close (+ conflict self-heal + lane skip-over)

**Close-path reordering** (`tick_donegate.py:474`): today
`verdict == achieved` (post CI-rewrite, which stays untouched at :408-467)
transitions straight to DONE. New order:

1. `merge_on_close.attempt(pr_url, workspace_dir)` — mechanical, via `gh pr
   merge --squash` (branch delete tolerated to fail; already-merged treated
   as success per FR-004; closed-unmerged → hard failure).
2. **merged** → existing close flow unchanged from :474 (ACHIEVE, ledger,
   views, auto-deploy, ping — the ping text gains "merged <sha>").
   Post-merge, best-effort workspace sync to default-branch head (the next
   goal's `prepare_ws` remains the guarantee; the sync is belt-and-braces
   for FR-005).
3. **conflict** and no heal attempted yet → set `merge_heal_attempted`,
   persist `pending_merge_pr`, dispatch ONE conflict-resolution increment via
   `_dispatch_action` (legal from `EXECUTING_IDLE`; distinct brief:
   "update goal/<id> onto <default> head, resolve conflicts, keep both
   sides' intent; verify must pass"). The normal cycle then re-delivers and
   the done-gate re-confirms (FR-017's re-confirmation comes free).
4. **conflict** with heal spent, or any hard failure → transition to BLOCKED
   with new kind `mechanical:merge_failed`, `blocked_on` naming the PR and
   the reason; `pending_merge_pr` + the achieved rationale persisted.

**Resume-retries-merge, not the gate** (FR-003): `resume_goal` fires UNBLOCK
→ `EXECUTING_IDLE` + replan poke (existing). New early branch in
`_handle_long_lived_advance`: if `pending_merge_pr` is set, re-attempt the
merge instead of building an advance brief; success → ACHIEVE close (legal
from `EXECUTING_IDLE`); failure → re-park. Zero cognition on this path.

**Lane skip-over** (FR-015): the project-lane predicate (`project_hold.py`)
treats a goal in `phase="blocked"` as NOT occupying its project lane, so the
next queued goal starts. Named test: a blocked predecessor + queued successor
→ successor dispatches next tick. (Today's behavior — blocked holds the lane
— is what idled the 08-28 night.)

**GoalStatus additions** (`models.py` + store): `pending_merge_pr: str`
(empty = none), `merge_heal_attempted: bool`. Ship the doctor check for the
schema change (spec 016 FR-014).

**Constitution amendment** (same PR): Principle V trust rationale →
"the goal-level done-gate's grounded evaluation is the merge authority; the
human reviews merged work after the fact and revert is the remedy."

### US2: devclaw self-deploy on merge

Net-new but thin on the Python side. After a successful US1 merge where the
goal's repo is the devclaw repo (match on the project's `repo_url`):

1. Record intent (`meta: deploy_pending = {sha, goal_id, ts}`).
2. A tick-path mechanical check (zero-token, after the cheap guards): if
   `deploy_pending` and `store.count_running() == 0` (running only — NOT
   `has_active_work`, which counts pending) and no dispatch is imminent →
   shell `gh workflow run deploy.yml -f <ref>` and mark
   `deploy_triggered`. Bounded wait: if quiescence hasn't arrived after
   `DEVCLAW_DEPLOY_QUIESCENCE_S` (default 6h), record loud, keep pending
   (re-armed on next devclaw close or operator resume — FR-009's "expires
   loudly").
3. `deploy-devclaw-auto.sh` (runs on the self-hosted runner, called by the
   workflow's auto lane): capture current running SHA from `/health`, run
   `deploy-devclaw.sh <new-sha>`; on its health-gate failure, re-run
   `deploy-devclaw.sh <previous-sha>` (exactly one rollback), exit non-zero
   so the workflow records failure. Rollback failure → the relay ping fired
   by the script is the instance-dead class (FR-010) — sent via the notify
   relay URL directly since the instance may be down.
4. The restarted instance's boot recovery (`queue.recover()`,
   `reset_running_to_pending`) is the existing safety net; quiescence gating
   makes it a no-op in practice.

Deploy outcomes land in `meta` (`deploy_last = {from_sha, to_sha, probe,
rolled_back, ts}`) — visible via `/node.json` freshness surface untouched.

### US3: quiet mode

- **State**: `meta` key `quiet_mode` = `{"until_ms": <epoch>, "armed_at": …}`
  — absence = off; corrupt = off (ControlPlaneMixin conventions, the
  `operator_hold` exemplar).
- **Filter**: `QuietNotifier(inner, store)` implementing the `Notifier`
  Protocol, bound at the single real choke point (`service.py:132-134`) —
  catches both `_notify` and the direct cycle-report send. `send(text)`:
  quiet armed → INSERT into `suppressed_pings`, return True; else delegate.
  `send_critical(text)`: always delegates (and records that it fired).
- **Critical class wiring**: the auth-pause ping site (`tick.py:887-909`)
  and US2's rollback-failure path call `send_critical`. Everything else
  untouched — the decorator does the work.
- **Operator verb**: `set_quiet_mode(on, until?)` in
  `server/tools/control.py` beside `set_operator_hold`; expiry self-disarms
  on read (a lazy check inside `QuietNotifier`, no timer). Suppressed
  backlog readable via the existing observability surface (one new read
  path returning the rows in order — FR-014).
- **New table**: `suppressed_pings(id, ts_ms, text)` + doctor check.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Reversal of #641 "a human merges" doctrine | Operator away a full week; unmerged PRs deadlock lanes (dispatch caps) and starve successors of predecessor work | Keeping human merges + raising caps rejected: successors would branch from stale main and the week's work would not compound |
| New blocked kind `mechanical:merge_failed` instead of reusing `needs_answer` | Resume must retry the MERGE (not the gate), so the kind must be mechanically distinguishable | Overloading `needs_answer` rejected: it would re-enter the planner and re-run the done-gate, violating FR-003 |
| Merge before the ACHIEVE transition (close path gains a subprocess) | FR-002: a goal must never read `done` with its PR unmerged | Merge-after-close rejected: the close's best-effort tail ("never undo a verified close") would make an unmerged-but-done goal legal state |
