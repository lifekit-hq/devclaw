# Proposal — the SDLC execution pipeline: reaching senior-team behavior

- **Status:** **LOCKED (direction)** — 2026-08-08. Captured from a live design
  conversation (the ledger compounding night-1 post-mortem → "how do we reach
  real-world execution?"), then clarified in the same session — all five `[OPEN]`s
  resolved by Denys in conversation (see "Clarify — resolved" below). Locking
  commits **direction only**, not schedule; a locked line stays reopenable (edit
  here, don't silently diverge). P1 (the accumulation seam) already SHIPPED (#486).
- **Date opened:** 2026-08-08 · **Authors:** Denys + Claude
- **Relates to / does not restate:** the north-star — devclaw should execute like
  a **real-world software team**: do as much as a capable model can per PR, but
  sized and sequenced the way a disciplined senior team would (SDLC + best
  practices), *not* hundreds of one-liner PRs (a temporary crutch) and *not* one
  giant unreviewable dump. Depends on the **grounded done-gate** and the
  **layer map** (`CLAUDE.md`), the worker-owned **PLAN.md** rolling-wave skill,
  the fail-closed gates (ADR 0007 strictness dial), and the **compounding
  experiment** (`proposals/compounding-experiment.md`) — this proposal is the
  *general frame*; that experiment is its first live test.

## The problem — devclaw executes, but not yet at senior-team quality

The loop plans → executes → gates → merges, but the *quality of execution* isn't
yet a disciplined engineer's. The 2026-08-07 ledger night-1 made the gap
concrete: the worker built all ten features in one **17k-line, 59-file PR** (a
junior "dump everything, call it done"), which then couldn't be reviewed and
never merged. The missing thing is **SDLC judgment** — knowing what a coherent
slice is, holding a real definition-of-done per slice, and sequencing merged
work — not raw capability (the model built ~90% of the app correctly).

## The hypothesis — prompts + reasoning + guardrails, as a pipeline

Senior-team behavior = **well-grained prompts + model reasoning + Python
guardrails**, organized as a **logical pipeline**. The three ingredients:

- **Prompts** — the *standard* and the *heuristic*: "what good looks like," "what
  a coherent slice is." They set the bar and teach judgment; they cannot enforce.
- **Model reasoning** — the *judgment calls*: what is the next coherent slice? is
  this PR one clean change? is the slice genuinely done?
- **Python guardrails** — deterministic *invariants / floors*: never ship
  unreviewed, the app must build, tests can't be deleted, one increment ≈ one
  slice not ten. They fail loud.

### The load-bearing principle — assign each decision to the layer that can ENFORCE it

The ledger lesson: "small, reviewable PRs" lived only in a **prompt** → it was
soft → the worker ignored it. So:

- **Invariants that must always hold → Python** (fail loud; the floor and the
  "never").
- **Judgment → model reasoning** (framed by a good prompt).
- **The standard / heuristic → prompts** (frame and teach; never the enforcement).

This is devclaw's existing **trust-the-input / verify-the-output**: reasoning does
the work, deterministic gates verify it.

## The pipeline — the SDLC loop; devclaw already has a version of every stage

We are **tuning an existing pipeline, not building one.** Each stage's decision
has an owner; the table names the current mechanism and today's gap.

| # | Stage | Decision | Owner | Current mechanism | Gap today |
|---|---|---|---|---|---|
| 1 | **Ground** | what's the repo state / what's left | reasoning | read repo + PLAN.md | ok; depends on stage 6 (was wiped by reset-to-main) |
| 2 | **Slice** | the next coherent feature/PR | **judgment** (prompt heuristic + reasoning) | PLAN.md rolling-wave; "advance one increment" | **weak — no heuristic bound + no guardrail; worker dumped all 10 features** |
| 3 | **Implement** | build it to a quality bar | prompt sets bar + reasoning | worker quality-bar prompt | ok |
| 4 | **Verify** | the slice's definition-of-done | **guardrail** | `verify_cmd` (build + tests) | per-slice DoD not enforced; "done" was self-declared |
| 5 | **Review** | reviewable diff, verdict | **guardrail** | fail-closed adversarial gate (ADR 0007) | crashes on an oversized diff (OOM `-9`) → a stage-2 symptom |
| 6 | **Merge** | land it, app stays runnable | seam | goal-branch accumulation / automerge | reset-to-main wiped unmerged work — **fix in flight (goal-branch for long_lived)** |
| 7 | **Iterate** | build on merged work | loop | heartbeat re-dispatch | only as good as stage 6 |

Last night's three failures map cleanly onto stages **2, 5, 6** — the pipeline
lens is the diagnostic: locate a gap at a stage, then decide prompt vs reasoning
vs guardrail.

## Where senior-vs-junior actually lives — and the trap

Senior-vs-junior is mostly **stage 2 (slice deliberately)** and **stage 4 (hold a
real definition-of-done)**. A junior dumps everything and calls it done. That is
devclaw's weakest area, and where the prompt-reasoning-guardrail combination must
be tightest.

**The trap — don't let guardrails absorb the judgment.** Encoding "senior slicing"
as rigid Python (a line-counter) fights the model's reasoning and produces the
one-liner fragmentation the north-star rejects. A 2k-line single-feature PR with
tests is fine; the same 2k split into 40 one-liners is worse. Guardrails are the
**floor and the "never"** (e.g. "this increment touches ten features → reject"),
**not** the judgment ("what's the right slice"). Keep them light
(good-design-is-light; systemic-over-specific).

## Clarify — resolved (2026-08-08)

- **[OPEN-1] The stage-2 slicing heuristic — RESOLVED.** The *heuristic* already
  exists in the worker's `PLAN.md` skill (`openhands-runner/skills/_writes-code/05-plan-md.md`:
  rolling-wave, de-risk-early, milestones→tasks, "advance by one solid increment").
  The gap is **enforcement**: the advance prompt (`tick.py:582`) never binds "one
  increment" to *one milestone*, and there is no guardrail. Fix = (a) sharpen the
  advance prompt + skill so one increment ≈ **the current PLAN.md milestone — do
  not build ahead**, and (b) a **light guardrail keyed on milestones, not lines**:
  flag when a single increment flips **>1 milestone** to `[x]` (or touches >1 SPEC
  feature). Judgment-respecting — the model picks the slice; the guardrail only
  catches "you did the whole plan at once." Never a line-counter (the trap).
- **[OPEN-2] Per-slice definition-of-done — RESOLVED.** A slice's DoD is the
  **existing gate on a bounded slice** — `verify_cmd` green + the fail-closed
  review gate + its milestone's tasks complete — no new gate. Slices **accumulate
  on the goal branch** and the **done-gate is the single cumulative review** at
  completion (see [OPEN-3]). The compounding hidden checklist stays **goal-level**
  (grades the whole `done_when`), never per-slice, preserving trust-input /
  verify-output (the worker never sees it).
- **[OPEN-3] Auto-merge reconciliation — RESOLVED: accumulate → merge at the
  done-gate.** Re-key the settle-time auto-merge SKIP from `bool(addresses)` to
  **"is this on a goal branch?"** (`resolve_strategy(...).goal_branch(...) is not
  None`). Then a long_lived goal-branch PR skips auto-merge and stays open for the
  done-gate — the same "one cumulative PR, reviewed once" contract checklist goals
  already have — **safe even if a project turns auto-merge on** (closes the #486
  deferred risk). Incremental slice-merges to `main` are the named end-state
  ([OPEN-4] evolution), not P1.
- **[OPEN-4] Moving target — RESOLVED.** The slicing bound is a **config value,
  not a hardcoded constant**, revisited on a **frontier-model release** (tie to the
  `devclaw-relevance-audit` cadence) or when the compounding scorecard shows
  plateau/churn attributable to slicing. Incremental-merge-to-main is the recorded
  north-star evolution to revisit here.
- **[OPEN-5] Relationship to the decomposer — RESOLVED: PULL.** Stage-2 slicing
  lives in the worker's `PLAN.md` (pull), consistent with the cognition-demolition
  arc (P3b). The control-plane **decomposer stays the `one_shot`-only dial**; we do
  **not** reintroduce a push planner.

## Sizing — slice, don't estimate

Per spec-lifecycle: this is a *direction*, sized as increments, not a whole-arc
estimate.

- **P1 — SHIPPED (#486):** the accumulation seam — goal-branch mode for
  long_lived — so stage 6 works and sequencing across nights is even possible.
  The floor; without it no other stage's improvement compounds.
- **P2 (next, firmable now):** stage-2 slicing — (a) sharpen the advance prompt +
  `PLAN.md` skill to bind "one increment" to the current milestone, and (b) the
  milestone-keyed mega-dump guardrail, enforced via the **ADR 0007 trust dial**
  (advise under `trust`, block under `strict`). Plus the [OPEN-3] auto-merge
  re-key (skip goal-branch PRs), a small companion. ~2 PRs.
- **P3 (named, unsized):** the OOM-admission floor that keeps the stage-5 review
  gate off the cliff (the `-9` class fix, 08-03 scoped); and — as the north-star
  evolution ([OPEN-4]) — incremental verified-slice merges to `main`.

The compounding experiment is the live instrument that tells us whether each
increment actually moves devclaw toward senior-team cadence.
