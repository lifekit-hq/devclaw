# Overnight autonomy — a goal never blocks-and-waits during the run window

**Status: DRAFT** (2026-07-30) — direction not yet locked; `[OPEN]` items below must be
answered or explicitly deferred-with-owner before this can flip to LOCKED.

## The problem this exists to kill

Every night the owner sets a goal, and every morning there is *something* waiting
on him: a review-gate crash, an environment failure, a package-lock error — a
blocker that fired at 10pm and **parked the goal until he woke up**. The pain is
not that an error happened (errors are unavoidable — CI flakes, real engineers
hit env issues and write bugs). The pain is that **the error reached a human
before morning.**

That is not a defect — it is the current design working as specified. devclaw's
Tranche-0 posture is *loud failure over silent degradation*: a block sets
`blocked_on`, pings the owner, and waits. That is exactly right for the phase of
**finding and fixing the walls** — you want to see every one. But it is the
opposite of what an **unattended overnight operator** needs. The system is
optimised to be legible to a developer, not autonomous for an operator. Those are
two different products, and the overnight run wants the second.

## The direction

**During the run window, a goal must never block-and-wait on a human.** When it
cannot make progress on an item:

1. retry with backoff (the existing heal/backoff machinery), then
2. if it still cannot proceed, **abandon that item, move to the next**, and
3. record the abandoned item + reason into the **morning summary**.

The goal only truly parks if *no* item remains workable. The owner wakes to a
report — "shipped A and B; couldn't do C because X" — **never** to a mid-night
"stopped, please advise." A blocked goal at 3am is a design failure for an
overnight operator, even when the individual block was legitimate.

The measured target this unlocks — the actual definition of a **clean run**:

> Over a run window, did *anything* reach the owner before morning? yes/no.

Not "did it complete the whole goal perfectly" (unreachable, and chasing it is
the source of the demoralisation). Clean = *it ran, shipped what it could, told
the truth about the rest, and did not need a human.*

## Interaction with the invariants — the headline

This **recalibrates the Tranche-0 escalation posture for the overnight window**;
it does **not** repeal the fail-closed gates. Precisely:

- **Fail-closed gates are untouched.** Nothing unverified ships. Verify /
  test-integrity / done stay always-hard; an unreviewable diff still fails closed.
  The change is *what happens after a fail/block* — the item is **skipped and the
  goal continues**, instead of the goal **parking for a human**.
- It is the same shape as the existing `mechanical:*` self-heal (blocks that
  clear themselves, zero-LLM, damped by a heal budget) — this generalises the
  "don't wait for a human overnight" principle from mechanical blocks to
  *work-item failure*.
- This is a genuine behavior change to the block/escalation contract, so per
  `spec-lifecycle.md` it starts here as a proposal — **no code before LOCK.**

## `[OPEN]` — the clarify step (must be resolved before LOCK)

- **[OPEN] Which block kinds may skip-and-continue vs must still wait?**
  `mechanical:*` already self-heal. `needs_answer` (a firming `unknowns` question)
  is genuinely human-gated — overnight, does the goal skip that item, defer the
  whole goal to morning, or make a bounded assumption and proceed? `bug` /
  `lost_ref` / `dispatch_cap` — skip-item or park?
- **[OPEN] Retry/backoff budget per item before abandon** — how many attempts,
  what backoff, before an item is declared un-workable-tonight? (Reuse the
  circuit-breaker's 3-strike, or a separate budget?)
- **[OPEN] Does "abandon item, continue" apply to `one_shot` checklist goals,
  `long_lived`, or both?** The `one_shot` batch is where wedges cluster; that may
  be the P1 boundary.
- **[OPEN] Relationship to the circuit breaker.** Today 3 straight failures →
  park for the owner. Does skip-and-continue *replace* that park with
  skip+summarise, or complement it (breaker still fires but into the morning
  report, not a live ping)?
- **[OPEN] Morning-summary surface.** Is the existing cycle-report
  (`/evals/cycles.json`) + deliveries tail enough to carry "shipped / abandoned +
  why", or is a dedicated morning digest needed? (The `devclaw-status` skill
  already assembles most of this.)
- **[OPEN] The all-abandoned case.** If every item is abandoned overnight, does
  the goal ping the owner immediately, or still wait for the morning report?

## Sizing — slice, don't estimate

- **P1 (firm this only):** the minimal *skip-item-and-continue-during-run-window*
  contract for **`one_shot` checklist goals** — the shape that wedges most — with
  abandoned items surfaced in the existing morning cycle-report. Success metric:
  over one week of nightly runs, **zero goals reach the owner before morning**
  (measured yes/no per window). `[OPEN]`-set above decides the P1 boundary.
- **P2 / P3 (named, unsized):** extend to `long_lived` goals; the `needs_answer`
  overnight policy; a dedicated morning digest if the cycle-report proves
  insufficient.

## Relationship to the other clean-run levers

This is lever #1 of three from the 2026-07-30 "how do we get a clean run"
conversation. The other two are being done directly (they are hardening, not a
behavior change, so they need no proposal):

- **Kill the top-3 recurring error *classes*** — review-gate crash (route
  signal-death into the existing #381 per-file ladder), environment/devcontainer
  (#441), `claude` flakiness (#422). Reduces the *rate* of walls.
- **Pick goals shaped to win** — the board shows bounded backend goals already run
  clean (`todo-*`, `closeloop-2026-07-05` all `achieved`); the wedges cluster on
  big open-ended goals, frontend/browser repos, and devclaw self-modification.

Lever #1 (this proposal) is the highest-value for the owner's *actual* pain
("waits for my answer until I wake up") — it changes a 10pm block from a
wake-me-up into a morning line-item. The class-kills reduce how often a wall is
hit at all; this changes what a hit *costs the human*.
