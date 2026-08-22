# Implementation Plan: Unit of Work & Planned Parallelism — US3 (`[P]` fan-out)

**Branches**: `feat/010-us3-planned-fanout` (increment 1) → `feat/010-us3-fanout-scheduler`
(increment 2, stacked) | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Slice**: US3 / FR-101…FR-105 in FULL. P1 (FR-001…FR-009, the derived
single-writer project hold) is already merged and is NOT touched here.

**Sequencing note**: the spec marks US3 "P3 — named-unsized, do not build until
P1 lands and earns it". The owner has directed that the remaining spec work be
implemented now, so this plan proceeds — and takes the spec's caution as a
design input rather than ignoring it: the machinery ships complete, and the
concurrency itself is opt-in (see *Default stance*).

**Clarify**: SKIPPED by owner direction (do not block). Every judgement call the
clarify step would have asked about is recorded under *Assumptions* below.

## Summary

US3 makes concurrent execution of increments legal on ONE project — but only for
increments the *plan* declared independent, only with a declared file scope, and
only with serial integration. Five mechanisms, shipped as a two-PR stack:

| FR | Mechanism | Increment |
|---|---|---|
| FR-103 | settle-time diff-vs-declared-scope enforcement, zero-LLM, fail-closed | **1** |
| FR-104 | spec-directory names allocated at planning time, never claimed at runtime | **1** (enforcement) + **2** (allocation) |
| FR-105 | a worker never spawns workers | **1** (standing guard) + **2** (degree from plan × host caps) |
| FR-101 | concurrent execution of `[P]` tasks with declared scopes | **2** |
| FR-102 | serial merge-queue integration onto the goal branch | **2** |

Increment 1 is the contract's enforcement; increment 2 is the executor that
relies on it. That order is deliberate — see *Why this order*.

## Default stance — fan-out is OFF by default, behind a real opt-in

The scheduler ships complete and is gated by one env dial,
`DEVCLAW_FANOUT` (default off). With it off, a goal dispatches exactly one
increment at a time, exactly as today; **a plan with no `[P]` scope declarations
behaves byte-identically to today whether the dial is on or off** (the scope
gate self-skips, and the planner finds no fan-out group so it takes the ordinary
single-dispatch path).

The opt-in is real, not decorative: the tests exercise both positions of the
dial, including the spec's Independent Test with it on.

Three reasons to ship it off:

1. **The spec's own caution.** P3 is "the earned exception… built only after the
   single-writer default has run in production". P1 merged today. A dial lets
   the owner earn it on a chosen night instead of inheriting it on a redeploy.
2. **It multiplies spend per goal.** Two lanes are two sandboxes and two agent
   sessions against one OAuth account, interacting with the quota-pause
   machinery. That is a decision an operator should make deliberately.
3. **It changes a goal's dispatch shape** from one task to a program of lanes,
   which every settle/observability surface downstream sees. An instance should
   adopt that on purpose.

## Why this order (enforcement before executor)

FR-103 is the reason FR-101 is safe at all, in two distinct ways:

- A declared scope that nothing checks is a soft constraint, and workers route
  around soft constraints (#358). Shipping the executor first would give us a
  window in which fan-out ran on an honour system.
- Disjoint declared scopes are also what make FR-102's serial integration
  *conflict-free*: two lanes that each stayed inside their own paths merge
  cleanly by construction. The enforcement is not a safety net bolted onto the
  scheduler; it is the scheduler's precondition.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none new — stdlib only

**Storage**: increment 1 adds nothing. Increment 2 adds ONE nullable column,
`tasks.lane_json` (the lane's position, declared scope, and integration
workspace), via the existing idempotent-ALTER migration list.

**Testing**: pytest, fully stubbed. New `tests/test_declared_scope.py`,
`tests/test_scope_gate.py` (increment 1); `tests/test_fanout_plan.py`,
`tests/test_merge_queue.py`, `tests/test_fanout_integration.py` (increment 2).
Baseline to beat: 2075 passed, 4 skipped.

**Target Platform**: Linux host. Layer 4 (TaskQueue gate chain + lane execution)
and the `devclaw/loom/` substrate; layer 2 only for the dispatch decision.

**Performance Goals**: the scope check consumes the diff `_IntegrityGate` already
computed and adds ONE `git status` — on the contract path only, so an increment
that declared nothing pays nothing. The fan-out planner is one directory glob
plus a file read, on the dispatch path only (never on an idle tick).

**Constraints**: zero LLM anywhere in this slice; fail closed on a violation AND
on an unreviewable check; byte-identical for plans with no `[P]` markers.

**Scale/Scope**: 4 new modules, ~2 touched layers, ~50 named tests, 2 PRs.

## Where each mechanism belongs

The layer map decides this.

- **FR-103 is layer 4, in the gate chain.** By the time layer 2 settles a goal
  action, the increment has already delivered — blocking there fails the GOAL
  and the out-of-scope change has already shipped. FR-103 says the *increment*
  fails, and `task_queue.run_pipeline` is the one place a verdict stops an
  increment before `deliver_change`. `_IntegrityGate` is the exact precedent: a
  pure, zero-LLM, always-hard scan over the same shared diff.
- **The parser is substrate** (`devclaw/loom/declared_scope.py`), beside
  `test_integrity.py` — engine-agnostic, no I/O, trivially unit-tested.
- **FR-101's decision is layer 2** (the goal decides *what* to dispatch, from
  plan data), but its *execution* is layer 4 (the queue owns concurrency and
  caps). The goal hands the queue an already-planned DAG; it never launches
  anything itself.
- **FR-102 is layer 4**, at the point delivery happens, because integration is
  part of shipping an increment — not something a later heartbeat discovers.

## Key design decisions

### Increment 1 — the contract

1. **The declaration rides the existing task graph.** A `[P]` row in
   `specs/*/tasks.md` declares its scope inline:

   ```text
   - [ ] T012 [P] [US1] Add the widget renderer (scope: src/widget/**, tests/test_widget.py)
   ```

   Parallelism stays *data in the plan* (the spec's framing) and there is no
   second artifact to keep in step. speckit's template already puts file paths
   in the row text; this formalises the subset that is a contract.

2. **The verdict is a pure function of its inputs.** Both — which `[P]` rows
   this increment newly checked, and which paths it touched — come from the
   unified diff the gate chain already computed, so the parser itself does no
   I/O, holds no state, and spends no token. The gate adds exactly one probe on
   top (the completeness read below), and only once a contract applies.

3. **Two ways to be bound.** A lane is bound by the scope the host PINNED at
   dispatch (increment 2); anything else is bound by its own claim on the task
   graph. The pinned form matters: a lane that never checks its row off must not
   thereby escape its declared I/O.

4. **Newly-checked, keyed on task id** — the same id-keyed rule
   `slice_guard.count_slice_advances` uses, so a row re-worded in the same
   increment still counts.

5. **Applicability is the opt-in.** No claim and no pinned scope ⇒ no contract ⇒
   pass. This is a gate that is *not consulted*, which under constitution V
   produces no silence to ship on.

6. **When it applies, it is total.** Allowed = the union of every claimed
   scope, plus the `specs/*/tasks.md` files themselves (a worker must be able to
   check off its own row). Anything else is a violation — including work
   smuggled under an unscoped task in the same increment, because letting an
   undeclared task widen the allowed set is a one-line route-around.

7. **Always-hard.** `"scope"` joins `ALWAYS_HARD`. An increment that left its
   declared scope has invalidated the premise of its own concurrency, not merely
   produced a finding a human could weigh at the merge boundary.

8. **Fail closed when unreviewable.** The parser is total, so the branch should
   be unreachable; if the gate is consulted and still cannot decide, it fails.

8b. **The judged span is the workspace's, not the agent's bookkeeping.** See
    *Dependency* below — the gate folds in unrecorded changes so a lane cannot
    escape its scope by declining to commit.

9. **Ordering: `verify → test_integrity → scope → [review] → browser`.** Scope
   is a pure scan, so running it early puts a hermeticity violation ahead of
   every `claude` call. For a plan that declares nothing, inserting a
   self-skipping pure function changes no verdict and no diff computation.

### Increment 2 — the executor

10. **The fan-out group comes from the plan, and only from the plan.**
    `goal/fanout.py` reads the active feature's `tasks.md`, takes the unchecked
    `[P]` rows of the earliest phase that still has work, and keeps them only if
    EVERY one declares a scope and the scopes are pairwise disjoint. Fewer than
    two survivors ⇒ no fan-out, ordinary dispatch. Zero LLM: it is a glob, a
    file read, and a string parse.

11. **Degree = plan ∧ host caps (FR-105).** The group is truncated to
    `MAX_CONCURRENT_PER_PROGRAM` (`DEVCLAW_MAX_CONCURRENT_PER_PROGRAM`, default
    2), and the queue independently honours `GLOBAL_MAX_CONCURRENT`. Nothing
    inside a sandbox has any say — and the standing FR-105 guard test pins that
    the sandbox's baked MCP config exposes no devclaw surface to ask from.

12. **A fan-out dispatch is a PROGRAM of lanes, so the goal keeps ONE in-flight
    ref.** `start_planned_program` already accepts a caller-supplied DAG with no
    planner (zero LLM), the queue already runs sibling tasks concurrently under
    its caps, and `_poll_program` already aggregates children into the
    `PollResult` layer 2 settles. So the whole scheduler is: build N
    `PlannedTask`s with no deps, hand them over, and let the existing machinery
    run them. No new phase, no multi-ref `GoalStatus`, no new settle path.

13. **Each lane gets its own workspace.** Two agents cannot share one checkout.
    Lane *k* runs in `<goal-workspace>/../<goal-ws-name>.lanes/<task-id>`,
    prepared from the goal branch by the same `prepare_ws` the ordinary dispatch
    uses. That is the hermetic boundary the declared scope describes.

14. **The merge queue is a real queue, not a lock** (FR-102). `loom/merge_queue.py`
    admits lane *k* only when every lane before it has finished integrating —
    strictly in plan order, one at a time, and a lane that fails releases its
    slot so later lanes are never wedged behind it. Bors' "not rocket science
    rule", in ~60 lines.

15. **Integration is local git, not a remote race.** At its turn a lane commits
    its own tree, then the shared goal workspace fetches from the lane
    *directory* and merges. No push contention, no credentials in the lane, no
    force-push. Delivery then runs ONCE per lane from the shared workspace, so
    the goal branch and its single cumulative PR behave exactly as they do for
    sequential increments. A merge conflict fails that lane loudly (and with
    disjoint declared scopes, enforced in increment 1, it cannot happen).

16. **FR-104 gets both halves.** The allocation half: the lane brief the host
    builds names the spec directory the task graph already allocated and forbids
    creating another. The enforcement half: a runtime-invented `specs/NNN-…/` is
    by construction outside every declared scope, so it fails the gate. Note
    this brief is built at layer 2 as Action text — it does NOT add a second
    home for worker-kind instructions (constitution II).

## Dependency: FR-103's guarantee needs a COMPLETE judged span (#630 / spec 013)

Raised during implementation and worth stating plainly, because it bounds what
FR-103 promises.

Two parts of devclaw compute "what did this increment change?" and they
disagree. **Delivery computes it mechanically** — it stages everything in the
workspace before committing, so it cannot miss a file. **The gates computed it
by trusting the agent** — `git diff <pre-run>`, which shows only what the agent
chose to record. Measured live on 2026-08-22 (task `78201bce`): delivery shipped
4 files / +179 lines while the gates judged 1 file / +32. A change made entirely
of new unrecorded files reaches the gates as an empty span and passes all of
them trivially.

For a scope gate that is the whole ball game: an increment would escape its
declared scope by simply not committing the offending file — the #358
route-around arriving through the back door, which would make the "always-hard"
classification read stronger than it is.

**What this slice does about it.** Rather than only document the gap, the gate
consults the workspace directly: when — and only when — a declared scope
applies, it folds `git status --porcelain --untracked-files=all` into the judged
span. One extra subprocess, on the contract path only, so an increment that
declared nothing pays nothing and the ordinary path stays byte-identical down to
its subprocess count. A fan-out lane is therefore held to its pinned scope
whether or not its worker records anything.

**What remains.** An increment with NO pinned scope is bound only by its own
*claim* on the task graph, and that claim is read from the recorded diff: if an
agent records literally nothing, there is no claim, so no contract. Closing that
needs the change materialised mechanically before any gate reads it — which is
exactly [spec 013 / #630](../013-materialize-change/spec.md), owned separately.
The interaction is pinned by
`test_an_increment_that_records_nothing_at_all_claims_nothing`, written to
document today's behaviour so that when 013 lands the test flips to asserting the
violation IS caught, and nobody rediscovers this by accident.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I. OAuth only** | PASS — unaffected | No new spawn site; lanes launch through the existing queue path, which already strips the keys. |
| **II. Model-agnostic worker** | PASS | Nothing is added to `runner/`. The declaration lives in the repo's own markdown task graph; the lane brief is Action text built by layer 2, not a second copy of a worker skill. The FR-105 guard test actively protects the sandbox seam. |
| **III. Zero-token idle** | PASS | Every mechanism here is pure string/git work. The fan-out planner runs on the dispatch path only — after the phase gates — so no idle or blocked tick gains work, and every `FakeClaude.calls == 0` guard stays green. |
| **IV. Single writer to state** | PASS | The gate is a pure verdict producer (no `mark_*`, no store write). Lane rows are created by the TaskQueue, which remains the only writer of task rows; layer 2 hands over a plan and never touches the queue's tables. |
| **V. Verification fails closed** | PASS — strengthened | A new always-hard gate; a violation blocks, an unreviewable consulted check blocks, and a lane that cannot integrate fails rather than ships. |
| **VI. Loud over silent** | PASS | The verdict names every out-of-scope path and the scope it was measured against; a failed integration names the lane and the conflict; the fan-out decision is logged with its lanes. |
| **VII. Fix the class** | PASS | The class is "a worker's actual I/O drifting from its declared I/O". Enforcing declared scope generally is why FR-104 needs no machinery of its own, and why the merge queue needs no conflict-resolution intelligence. |

**Gate result**: PASS. Complexity Tracking empty.

## Project Structure

```text
devclaw/
├── loom/
│   ├── declared_scope.py     # NEW (inc 1) — parse [P] scopes + changed paths; match; verdict
│   └── merge_queue.py        # NEW (inc 2) — serial admission in plan order
├── delivery/
│   └── integrate.py          # NEW (inc 2) — commit a lane, merge it into the shared workspace
├── goal/
│   ├── fanout.py             # NEW (inc 2) — the plan → lanes decision + the lane brief
│   ├── engine.py             # (inc 2) dispatch_fanout → start_planned_program
│   └── tick_dispatch.py      # (inc 2) the fan-out branch of the dispatch choke point
├── task_queue.py             # (inc 1) _ScopeGate; (inc 2) lane execution + integration
├── program_plan.py           # (inc 2) PlannedTask carries a workspace + lane spec
├── state_store/              # (inc 2) tasks.lane_json
└── quality/
    ├── gate_pipeline.py      # (inc 1) GateInput.declared_scope
    └── gate_policy.py        # (inc 1) "scope" is ALWAYS_HARD

tests/
├── test_declared_scope.py    # NEW (inc 1)
├── test_scope_gate.py        # NEW (inc 1)
├── test_fanout_plan.py       # NEW (inc 2)
├── test_merge_queue.py       # NEW (inc 2)
└── test_fanout_integration.py# NEW (inc 2) — the spec's Independent Test
```

**Structure Decision**: layers 2 and 4 only. No engine/sandbox change, no worker
change, no change to the increment's definition — which is what the spec's Out
of Scope demands.

## Assumptions

Recorded in place of the skipped clarify step. Each is a call the owner may
reverse; none is load-bearing beyond this slice.

1. **Declaration syntax** is `(scope: <comma-separated globs>)` inline in the
   task row — the contract stays in the task graph rather than a second file.
2. **Glob semantics**: `*` does not cross `/`, `**` does, `?` matches one
   non-`/` character, and a declaration naming a directory covers its subtree.
   The directory tolerance is the one deliberately permissive choice: it widens
   only what the plan itself named.
3. **Mixed increments fail.** An increment that claims a scoped `[P]` row and
   also edits files for an unscoped task is a violation.
4. **`specs/*/tasks.md` is always in scope** when the check applies; nothing
   else is implicitly allowed.
5. **`"scope"` is always-hard**, never dial-able.
6. **Fan-out is off by default** behind `DEVCLAW_FANOUT` (see *Default stance*).
7. **A fan-out group needs ≥2 lanes and pairwise-disjoint declared scopes**;
   anything less takes the ordinary single-increment path. Disjointness is
   decided syntactically (no glob may cover another's literal prefix) — a
   conservative test that can only decline to fan out, never wrongly permit it.
8. **Lane workspaces are siblings of the goal workspace** (`<name>.lanes/<task>`)
   and are prepared from the goal branch, so a lane starts from exactly the
   state a sequential increment would have started from.

## Complexity Tracking

*No constitution violations — intentionally empty.*
