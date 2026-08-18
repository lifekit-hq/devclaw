# Flow — the autonomous issue-driven pipeline (PLANNED)

> **STATUS: PLANNED — not yet built.** This describes the target-state flow once
> the P1–P3 arc lands. It is the narrative companion to the specs, not a trace of
> current behavior. Do not trust it as CURRENT until the specs below are implemented
> and this banner is removed.
>
> - **P1** — `specs/006-intake-readiness-gate` (specify + clarify done)
> - **P2** — `specs/007-autonomous-issue-dispatch` (scoped + clarify done, unsized)
> - **P3** — `specs/008-speckit-execution-substrate` (substrate LANDED — speckit drives execution, host planning chain removed; label-routing still open)
> - **Adoption** — `specs/009-universal-issue-adoption` (**SHIPPED**): the grade accepts
>   ANY open issue — hand-written backlogs included — via `regrade_intake`, plus a
>   batch-capped `grade_backlog` for onboarding a whole backlog. The door below is
>   no longer the only entrance to Stage 1.
>
> Interactive version: an Artifact was generated from this doc (ask the owner for the link).

The one-line shape: **an ask enters one door (or an existing issue is adopted
as-is — spec 009), is graded for readiness, is dispatched
(by a human, or autonomously once you flip a flag), is scoped by speckit, built one
slice per PR, gated, and merged by a human — with the GitHub issue as the source of
truth throughout and PLAN.md gone.**

---

## The pipeline

```
   anyone (you, an agent, Telegram)
                 │
                 ▼
  STAGE 0 · THE DOOR · file_intake                         [exists]
    structural gate: what + done_when≥20 + provenance
    → GitHub issue created = receipt
                 │
                 ▼
  STAGE 1 · READINESS GRADE                                [P1]  (async)
    also enters here: any EXISTING open issue, any format,
    via regrade_intake / grade_backlog (spec 009)  [exists]
    ground the ask vs the repo: locatable surface +
    concrete change + verifiable intent?  fail-closed →
        ├── devclaw-ready ─────────────────┐
        └── needs-refinement (+reason) → human refines → re-trigger
                                            │
                                            ▼
  STAGE 2 · DISPATCH                                        [P2]
    flag OFF (default) → a human dispatches
    flag ON → the tick claims (guard-safe):
      • cheap SQLite check BEFORE any cognition/network
      • provenance wall: self-filed needs human promotion;
        human/external flows
      • triage: priority label, then oldest
      • CAS claim (no double-dispatch), dispatch cap held
    → create_goal(issue)          label: in-progress
                                            │
                                            ▼
  STAGE 3 · SCOPE via SPECKIT                               [P3]
    goal lifecycle: executing (goals are born executing)
    label-routed:
      feature/enhancement → specify→plan→tasks (specs/NNN/)
      bug/chore/docs       → direct-advance, no spec
    no interactive clarify; can't scope → bounce needs-human
    NO PLAN.md (speckit is universal: adopt, or install via PR)
                                            │
                                            ▼
  STAGE 4 · IMPLEMENT, one slice per PR
    worker does the CURRENT tasks.md slice only
    slice-guard reads tasks.md checkbox flips [P3] (not PLAN.md)
    gate chain [exists]: verify → test-integrity → review(dial) → delivery
                                            │
                                            ▼
  STAGE 5 · DELIVER                                         [exists]
    commit → branch → push → PR (devclaw label + Summary/Testing body)
    broken delivery = fail, never "done without a PR"
                                            │
                                            ▼
  STAGE 6 · GATE & DONE
    ⟵ YOU MERGE (backstop; devclaw never self-merges)
    goal proposes done → grounded done-gate vs done_when [exists]:
      achieved → close the issue
      not      → re-steer / needs-human
```

Tags: **[P1]/[P2]/[P3]** = the slice that delivers the step; **[exists]** = current
devclaw machinery reused unchanged.

---

## Walk one ask through it

Example: *"Add a 30-day cash-flow forecast + shortfall sentinel to finance-sentry"*
(the real issue #430).

1. **Door.** `file_intake(finance-sentry, what=…, done_when="backend computes a 30-day
   forecast; a shortfall sentinel is exposed via the API; tests cover
   income/expenses/low-cash")`. Structural gate passes → **issue #430 created**, receipt
   URL returned. *[exists]*
2. **Grade.** The readiness validator snapshots the repo and checks: locatable surface
   (the Wealth/Alerts modules), concrete change (a forecast service + endpoint +
   sentinel), verifiable intent (the done_when) → **`devclaw-ready`.** *[P1]*
3. **Dispatch.** Flag is ON. The tick's cheap check sees a claimable ready issue,
   human-filed (no promotion needed), highest priority → **claims it (CAS)** →
   `create_goal(#430)`, label → **in-progress**. *[P2]*
4. **Scope.** The `feature` label routes the worker's first advance to **speckit**:
   `specs/030-cashflow-forecast/` with spec.md, plan.md, and a `tasks.md` — T001 forecast
   service, T002 sentinel rule, T003 API endpoint, T004 tests (some marked `[P]`). *[P3]*
5. **Implement.** The worker does **T001 only**; the slice-guard watches `tasks.md` and
   blocks any attempt to also complete T003. T001 → verify → review → **PR #1**. Next
   dispatch: T002. One coherent slice, one reviewable PR each. *[P3 + exists]*
6. **Deliver + merge.** Each PR lands with the devclaw label and a Summary/Testing body.
   **You review and merge** — devclaw cannot merge itself. *[exists]*
7. **Done.** The goal proposes done → the done-gate re-checks against the done_when
   (forecast computes? sentinel exposed? tests present?). Achieved → **issue #430
   closes.** Otherwise it bounces to **needs-human** with the gap. *[exists]*

---

## The branch points (the other paths)

- **Ungroundable ask** ("make finance-sentry better") → Stage 1 → **`needs-refinement`**
  with "no locatable surface / no concrete change." Never looks dispatchable. You
  sharpen it and re-trigger the grade. *[P1]*
- **Self-filed ask** (devclaw's own self-issue-filing) → graded ready, but at Stage 2 the
  **provenance wall** holds it until *you* promote it. No self-dealing. *[P2]*
- **A bug** (`fix: forecast off-by-one`) → Stage 3 routes it **direct-advance, no spec** —
  no speckit ceremony on a one-liner. *[P3]*
- **Can't scope cleanly** → the scope step fails → **needs-human**, never a garbage plan. *[P3]*
- **Flag OFF** (today, and until you trust it) → Stage 2 is just *you* dispatching a
  `devclaw-ready` issue. Everything else identical.

---

## The label state machine (GitHub-native = source of truth AND dashboard)

```
intake ─▶ [P1 grade] ─▶ devclaw-ready ──▶ [P2 claim] ─▶ devclaw-in-progress ─▶ closed (done)
                    └─▶ needs-refinement                                     └─▶ needs-human
```

---

## Where you sit — and how it shrinks

| Control point | Stays yours? |
|---|---|
| Filing / refining asks | Shared (you or agents) |
| **Promoting self-filed issues** | **Always yours** — the anti-busywork wall |
| **Flipping the autonomy flag** | **Always yours** — off by default |
| **Merging every PR** | **Always yours** — the merge backstop |
| Handling needs-human bounces | Yours |
| Picking which ready issue to dispatch | Yours until the flag flips, then the tick's |

Autonomy grows by flipping **one flag**, and even fully on, **three hard human gates
remain**: promotion, activation, merge. That is the "companion → autonomy,
evidence-gated" arc made concrete.

---

## The phased reality (what's live when)

- **After P1:** the door grades everything; you still dispatch by hand. *Value: a
  trustworthy backlog.*
- **After P2:** flip the flag → the loop pulls `devclaw-ready` work itself, walled from
  self-dealing, you merge. *Value: hands-off execution of human-filed work.*
- **After P3:** execution runs on speckit per-feature, PLAN.md is gone, plans never
  bloat. *Value: it scales to many features without the monolith.*

---

## Rejected along the way (direction memory)

- **A pluggable planning "port"** (adapters for speckit / issues / PLAN.md) — rejected;
  speckit is universal since the owner controls every repo. Variation rides speckit's own
  `workflow-registry.json`, not a devclaw abstraction. (spec 008)
- **`taskstoissues` as an issue-creator / task-level execution unit** — rejected; it runs
  the wrong direction and would mint issues that bypass the readiness gate. The
  **feature-issue** stays the graded/claimed/done-gated unit. (spec 008)
- **Auto-enabling autonomy on a metric** — rejected; the human flips the flag (spec 007).
- **Async-clarify inside P2** — deferred to its own slice; P2 only dispatches
  already-graded work (spec 007).
