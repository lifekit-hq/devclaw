# Proposal — the SDLC execution pipeline: reaching senior-team behavior

- **Status:** **DRAFT** — 2026-08-08. Captured from a live design conversation
  (the ledger compounding night-1 post-mortem → "how do we reach real-world
  execution?"). Direction, not schedule; `[OPEN]` items below are unresolved and
  must be interrogated before any LOCK.
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

## `[OPEN]` — clarify before any LOCK

- **[OPEN-1] The stage-2 slicing heuristic.** What is the prompt-level definition
  of "one coherent slice," and what is the *light* guardrail that catches a
  mega-dump without fragmenting (e.g. "one increment ≈ one PLAN.md milestone," not
  a line count)? Owner: TBD.
- **[OPEN-2] Per-slice definition-of-done (stage 4).** How is "this slice is
  genuinely done" enforced beyond `verify_cmd` green — and where does the hidden
  checklist (compounding experiment) fit vs. the worker-visible bar?
- **[OPEN-3] Auto-merge reconciliation (stage 6).** The settle-time auto-merge
  signal (`bool(addresses)`) is not yet reconciled for goal-branch long_lived
  work (deferred in PR #486). What is the correct "ready to land a slice" signal?
- **[OPEN-4] Moving target.** Model one-shot capability keeps growing, so the
  right slice size moves. How/when do we revisit the bound rather than freeze it?
- **[OPEN-5] Relationship to the decomposer.** Does stage-2 slicing live in the
  worker's PLAN.md (pull) or a control-plane decomposer (push)? The demolition arc
  favored worker-owned; confirm this frame doesn't reintroduce a push planner.

## Sizing — slice, don't estimate

Per spec-lifecycle: this is a *direction*, sized as increments, not a whole-arc
estimate.

- **P1 (in flight):** the accumulation seam — goal-branch mode for long_lived
  (PR #486) — so stage 6 works and sequencing across nights is even possible.
  This is the floor; without it no other stage's improvement compounds.
- **P2 (named, unsized):** stage-2 slicing — strengthen the PLAN.md heuristic
  prompt **and** add a light guardrail against the mega-dump (resolve [OPEN-1]).
- **P3 (named, unsized):** stage-4 per-slice definition-of-done; stage-6
  auto-merge reconciliation ([OPEN-3]); the OOM-admission floor that keeps the
  stage-5 gate off the cliff.

The compounding experiment is the live instrument that tells us whether each
increment actually moves devclaw toward senior-team cadence.
