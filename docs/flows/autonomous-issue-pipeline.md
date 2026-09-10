# Flow — the issue-driven pipeline

> **STATUS (2026-09-10): LIVE, and stage 2 is human BY DESIGN; one story unbuilt.**
> Each step below is tagged with what the code does today. The specs behind it:
>
> - `specs/006-intake-readiness-gate` — **SHIPPED**: the door grades every ask.
> - `specs/007-autonomous-issue-dispatch` — **CUT 2026-09-10**
>   (`specs/README.md`). The heartbeat claiming its own issues is not planned
>   work: the autonomy ratchet that gated it reads `pass: false` at a 0.0
>   first-pass rate, and empty-queue idle was 37 minutes in 14 days. Filing a
>   goal from a graded issue is yours, deliberately, not pending.
> - `specs/008-speckit-execution-substrate` — **SHIPPED** for the substrate
>   (speckit drives execution in-sandbox, host planning chain removed);
>   **US3 label-routed ceremony is NOT BUILT**.
> - `specs/009-universal-issue-adoption` — **SHIPPED**: the grade accepts ANY
>   open issue via `regrade_intake`, plus a batch-capped `grade_backlog`.
> - `specs/023` (webhooks) — **ACTIVE** when `DEVCLAW_WEBHOOK_SECRET` is set.
> - `specs/025` (merge-on-close) — **SHIPPED**: see [delivery.md](./delivery.md).

The one-line shape: **an ask enters one door (or an existing issue is adopted
as-is), is graded for readiness, is filed as a goal by a human (self-fix pickup
is the one autonomous entrance), is planned by speckit in the sandbox, built one
slice per dispatch, gated, and squash-merged by the confirmed-achieved close —
with the GitHub issue as the source of truth throughout and PLAN.md gone.**

---

## The pipeline

```
   anyone (you, an agent, Telegram)
                 │
                 ▼
  STAGE 0 · THE DOOR · file_intake                         [exists]
    structural gate: what + done_when≥20 + provenance
    + the filer's expected_increments claim & its basis
      (optional; a count with no basis rejects) [spec 012 US3]
    → GitHub issue created = receipt, label devclaw-intake
                 │
                 ▼
  STAGE 1 · READINESS GRADE                                [exists]  (async)
    scheduled by file_intake itself; also enters here:
    any EXISTING open issue via regrade_intake / grade_backlog
    (spec 009), or an `issues opened/edited` webhook (spec 023)
    ONE cognition call, THREE independent axes:
    (a) ground the ask vs the repo: locatable surface +
        concrete change + verifiable intent?  fail-closed →
        ├── devclaw-ready ─────────────────┐
        └── needs-refinement (+reason) → human refines → re-trigger
    (b) validate the filer's increment claim (never overwrite it) →
        └── needs-sizing when no claim / unestimable / unassessable /
            disputed — a human decides. Never moves axis (a), and
            never selects a shape: every work item runs as a saga.
    (c) staleness: does the repo already satisfy the ask? →
        └── stale ⇒ needs-refinement, whatever (a) said — the work
            is already done, so dispatching burns a session to
            rediscover that. Uncertain ⇒ not stale; (b) never
            makes an ask stale.
                                            │
                                            ▼
  STAGE 2 · DISPATCH                                          [human by design]
    a human files the goal: create_goal(issues=[…])
    the one autonomous entrance is self-fix pickup [exists]:
      • a human puts `accepted` on a devclaw:self-filed issue
        (or on a human-filed issue marked devclaw:pickup)
      • the cycle edge opens ONE one_shot goal per accepted
        issue (zero LLM to detect; DEVCLAW_SELF_FIX_CONCURRENCY,
        default 1; gated on DEVCLAW_SELF_REPO) → label devclaw:fixing
    nothing claims a devclaw-ready issue on its own
                                            │
                                            ▼
  STAGE 3 · PLAN via SPECKIT, in-sandbox                    [exists]
    goal lifecycle: executing (goals are born executing)
    every dispatch runs the speckit flow (specs/NNN/ in the repo)
    label-routed ceremony (feature → full cycle, bug → direct)
                                    [spec 008 US3 NOT BUILT]
    spec dirs present but none graded → dispatch held at the boundary
    NO PLAN.md (speckit is universal: adopt, or install via PR)
                                            │
                                            ▼
  STAGE 4 · IMPLEMENT, one slice per dispatch               [exists]
    worker does the CURRENT tasks.md slice only
    slice-guard reads tasks.md checkbox flips (not PLAN.md)
    gate chain: verify → materialize → change_class →
      test_integrity → review (strict only) → browser (dial)
                                            │
                                            ▼
  STAGE 5 · DELIVER                                         [exists]
    commit → goal branch → push → ONE cumulative PR
      (devclaw label + Summary/Testing body + Closes #N)
    broken delivery = fail, never "done without a PR"
                                            │
                                            ▼
  STAGE 6 · GATE, DONE & MERGE                              [exists]
    goal proposes done → grounded done-gate vs done_when:
      achieved → CI green on the same head → squash-merge
                 (spec 025) → GitHub closes the issue (Closes #N)
      not      → re-advance; needs_human → typed Problem;
                 3 flat rounds → donegate_churn park
```

Tags: **[exists]** = live machinery; **[human by design]** = a step that stays
yours on purpose (spec 007, cut); **[spec 008 US3 NOT BUILT]** = specified and
not yet built.

---

## Walk one ask through it

Example: *"Add a 30-day cash-flow forecast + shortfall sentinel to finance-sentry"*
(the real issue #430).

1. **Door.** `file_intake(finance-sentry, what=…, done_when="backend computes a 30-day
   forecast; a shortfall sentinel is exposed via the API; tests cover
   income/expenses/low-cash")`. Structural gate passes → **issue #430 created**, receipt
   URL returned, grade scheduled. *[exists]*
2. **Grade.** The readiness validator snapshots the repo and checks: locatable surface
   (the Wealth/Alerts modules), concrete change (a forecast service + endpoint +
   sentinel), verifiable intent (the done_when) → **`devclaw-ready`.** *[exists]*
3. **Dispatch.** You file `create_goal(issues=[#430])`; the goal's contract is read
   live from the issue. *[human by design]*
4. **Plan.** The worker's first advance runs speckit in the sandbox:
   `specs/030-cashflow-forecast/` with spec.md, plan.md, and a `tasks.md` — T001 forecast
   service, T002 sentinel rule, T003 API endpoint, T004 tests. *[exists]*
5. **Implement.** The worker does **T001 only**; the slice-guard watches `tasks.md` and
   holds dispatch if pending tasks sprawl beyond the current feature. T001 → gate
   chain → **the cumulative PR**. Next dispatch: T002, stacked on the same PR. *[exists]*
6. **Deliver.** Every increment pushes to the goal branch; the one PR stays open for
   the whole goal (#486). *[exists]*
7. **Done + merge.** The goal proposes done → the done-gate re-checks against the
   done_when (forecast computes? sentinel exposed? tests present?). Achieved, and the
   PR's CI is green on that head → **squash-merged, issue #430 closes.** Otherwise it
   re-advances, or raises a **typed Problem** for you. *[exists]*

---

## The branch points (the other paths)

- **Ungroundable ask** ("make finance-sentry better") → Stage 1 → **`needs-refinement`**
  with "no locatable surface / no concrete change." Never looks dispatchable. You
  sharpen it and re-trigger the grade. *[exists]*
- **Disputed or unrecorded extent** → Stage 1 axis (b) → **`needs-sizing`** naming the
  reason. Orthogonal to readiness: a `devclaw-ready` + `needs-sizing` issue is
  dispatchable, it just has an extent a human should settle first. The count sizes
  the plan; it never selects an execution shape. *[spec 012 US3]*
- **Already-done ask** (the fix landed since the issue was filed) → Stage 1 axis (c) →
  **`needs-refinement` (stale)**, naming "the described condition appears to be already
  resolved in the repository." Overrides a clean grounding verdict: close the issue, or
  rewrite it to describe what is still missing, then re-grade. *[spec 028 US2]*
- **Self-filed ask** (devclaw's own self-issue-filing, `devclaw:self-filed`) → nothing
  picks it up until *you* add `accepted`; then the cycle edge opens one self-fix goal.
  No self-dealing. *[exists]*
- **A bug** (`fix: forecast off-by-one`) → runs the same speckit flow as a feature;
  the direct-advance shortcut is spec 008 US3. *[NOT BUILT]*
- **No graded spec** (dirs exist, no `tasks.md` anywhere) → the dispatch is held
  until the speckit plan step runs, never a garbage plan. *[exists]*  The old
  sibling-feature check that parked goals `mechanical:slice_hold` was retired
  (`specs/tiny/slice-guard-observes-the-goal`): it predicted build-ahead from
  leftover spec dirs, and in a per-goal checkout it parked goals behind their
  peers' live work. Build-ahead is caught on the settle path, where the goal's
  own commit makes it a fact rather than a guess.
- **Merge fails at close** → `mechanical:merge_failed` after one bounded conflict
  self-heal; the lane skips over to the queued successor. *[exists, spec 025]*

---

## The label state machine (GitHub-native = source of truth AND dashboard)

```
devclaw-intake ─▶ [grade] ─▶ devclaw-ready ──▶ [human create_goal] ─▶ closed (Closes #N on merge)
                         └─▶ needs-refinement          (needs-sizing rides orthogonally)

devclaw:self-filed ─┐
devclaw:pickup ─────┴▶ + accepted (human) ─▶ [cycle-edge pickup] ─▶ devclaw:fixing ─▶ closed
```

---

## Where you sit

| Control point | Stays yours? |
|---|---|
| Filing / refining asks | Shared (you or agents) |
| **Accepting self-filed / pickup issues** | **Always yours** — the anti-busywork wall |
| **Filing goals for `devclaw-ready` issues** | **Yours, permanently** — the autonomous claim (spec 007) was cut 2026-09-10 |
| Merging the cumulative PR | The confirmed-achieved close (spec 025); your review moves post-merge |
| Resolving typed Problems | Yours — `correct_implementation` / `decide` |

---

## What's live when

- **Now:** the door grades everything (spec 006/009, webhooks optional); you file
  goals; execution runs on speckit in-sandbox (spec 008); the done-gate closes and
  merges (spec 025), with CI as the verdict of record (spec 032).
- **Not coming:** the heartbeat claiming `devclaw-ready` issues itself — spec 007
  was cut 2026-09-10. The autonomy ratchet (`DEVCLAW_RATCHET_*`) survives it as the
  compounding-readiness signal; there is no longer a flip behind it.
- **Unbuilt:** label-routed ceremony (spec 008 US3).

---

## Rejected along the way (direction memory)

- **A pluggable planning "port"** (adapters for speckit / issues / PLAN.md) — rejected;
  speckit is universal since the owner controls every repo. Variation rides speckit's own
  `workflow-registry.json`, not a devclaw abstraction. (spec 008)
- **`taskstoissues` as an issue-creator / task-level execution unit** — rejected; it runs
  the wrong direction and would mint issues that bypass the readiness gate. The
  **feature-issue** stays the graded/claimed/done-gated unit. (spec 008)
- **Auto-enabling autonomy on a metric** — rejected while spec 007 lived, and moot since it was cut: there is no flag to flip.
- **Async-clarify** — devclaw asking its own clarifying questions on an issue. Never
  built; it was a slice of spec 007 and went with it.
- **A pre-merge cumulative review gate** — rejected; `done_when` is the sole pre-merge
  authority, human review moves post-merge (spec 025 FR-006).
