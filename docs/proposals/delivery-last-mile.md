# Proposal — delivery last-mile: the "PR merges, I get told, the goal pursues" contract

- **Status:** **DRAFT** — skeleton opened 2026-07-28; `[OPEN]` items await the clarify
  step (no code before lock).
- **Date opened:** 2026-07-28 · **Authors:** Denys + Claude
- **Grounded on:** the 2026-07-28 morning-after evidence (below) — live prod env
  verified (`DEVCLAW_GOAL_AUTOMERGE=1` in the running container), `merge.py` /
  `tick_settle.py` read on `main` @ `11a021b`, both open PRs inspected via `gh`.
- **Relates to:**
  - **#393** (dispatch-boundary freshness guard) and **#394** (delivery contract
    owns mergeability + branch hygiene) — the two mechanical class-fixes filed from
    the same incident. This proposal is the *policy* layer above them; they can land
    independently of it.
  - [ADR 0007](../decisions/0007-gate-strictness-dial.md) — under `trust`,
    review-advisory findings ship on the premise that **the human merge is the
    backstop**. Any widening of auto-merge must reconcile with that premise
    explicitly (see `[OPEN] O2`).
  - [ADR 0011](../decisions/0011-branch-target-delivery-seam.md) +
    [`v1-helper-resurface.md`](./v1-helper-resurface.md) — goal-less direct tasks
    exist now and deliver real PRs; they have **no merge policy and no visibility
    surface** (the P2 slice named there covers visibility; merge policy lands here).
  - [`issue-driven-pipelines.md`](./issue-driven-pipelines.md) — work-item
    provenance is the eventual home of the freshness/duplication class (#393 is the
    minimal mechanical slice).

---

## 1. The incident that names the gap (2026-07-28)

The night ran **clean** (cycle 2026-07-27: 12/12 settled, zero wedges) — and the
morning still produced: two CONFLICTING PRs, a night of quota spent duplicating
work the owner had already done by hand (fs PR #329, closed unmerged), an
auto-merge that silently never engaged despite `DEVCLAW_GOAL_AUTOMERGE=1`
(closeloop PR #8: no `auto-merged` / `auto-merge failed` log line all night), and
an owner who learned all of this by asking a session to dig, not from devclaw.

The mechanism-reliability war is essentially won; the frontier is the seam between
devclaw's output and the owner's workflow. The owner's stated contract:

> *"My expectation was that this PR gets merged and I just get info what has been
> done and the goal pursues."*

Three promises — **merge**, **inform**, **continue** — of which today only
*continue* reliably exists.

## 2. Ground truth — what already exists (don't rebuild it)

- **Auto-merge on gate-green exists and is ON in prod** (`goal/merge.py`,
  decision 2 of outcome-goals; `DEVCLAW_GOAL_AUTOMERGE=1` verified live
  2026-07-28). Per-project override via `Project.automerge`; strategy override via
  `merge_strategy`; `devclaw/gate` commit status posted so branch-protected repos
  can gate GitHub-native `--auto` on it.
- **A loud owner-ping on failed merge exists** (`tick_settle.py`, the 2026-07-17
  lesson) — but it only fires when the merge path *engages*. The pillar-2
  checklist-mode skip (`addresses` non-empty) bypasses merging entirely and logs
  nothing owner-visible; #394 tracks diagnosing which hole PR #8 fell through.
- **Cycle reports exist** (ADR 0006) — a per-cycle settled/wedge summary already
  reaches the owner; it reports *task outcomes*, not *PR states awaiting the
  owner*.
- **The done-gate is the designed safety net for merged work** (decision 2:
  "devclaw merges it itself and pings a plain summary; the done-gate is the safety
  net").

## 3. Direction (to be firmed at clarify)

One coherent last-mile contract, mostly composition of existing parts:

1. **Merge policy becomes legible and total.** Every gate-green delivery resolves
   to exactly one of `merged | left-for-owner(reason) | skipped(reason)` — and the
   resolution is *always* visible (goal log + notify tier by severity). No path
   where automerge-is-on ends in an open PR with no explanation. (Mechanical half
   = #394.)
2. **Merge policy reconciles with the strictness dial.** Candidate shape:
   auto-merge composes with `strict` naturally (surviving findings fail closed, so
   gate-green means clean); under `trust`, an advisory-flagged delivery is exactly
   the case the human backstop was designed for → `left-for-owner` with the
   findings attached, not an unconditional merge. `[OPEN] O2` decides.
3. **A "shipped overnight" owner report.** Extend the existing cycle report (or a
   sibling notification at window close) with the PR ledger: merged (with one-line
   summaries), awaiting-owner (with *why* — conflict, advisory findings, policy),
   and goal-less direct-task deliveries — the current invisible class. Zero new
   LLM on tick paths; it's a projection over state the store already has.
4. **Branch hygiene closes the loop.** After a goal-branch PR merges (owner or
   auto), prep re-seeds the goal branch so delivery N+1 starts landable (#394);
   dispatch refuses/flags stale bases (#393). Without these, promises 1–3 report
   failures that shouldn't exist.

## 4. Slices (per the sizing rule — firm P1 only at lock)

- **P1 (candidate):** total merge-policy resolution + the owner ledger in the
  cycle report. Composition + projection; no new gates, no tick-path LLM.
- **P2 (named-unsized):** strictness-aware merge policy (the O2 outcome), if it
  isn't already folded into P1 by the clarify step.
- Out of scope here: #393/#394 (independent mechanical fixes), console file-a-task
  + direct-task visibility (v1-helper P2), work-item provenance
  (issue-driven-pipelines).

## 5. `[OPEN]` items — the clarify step (all must resolve before LOCK)

- **[OPEN] O1 — What does "merge" mean under `trust`?** Auto-merge an
  advisory-flagged PR (done-gate as sole safety net), or always `left-for-owner`
  when any advisory finding survived? The ADR 0007 backstop premise says the
  latter; the hands-off decision-2 vision says the former. Owner call.
- **[OPEN] O2 — Does auto-merge stay a repo/ops switch, or become
  strictness-coupled?** Today it's env + per-project. Coupling it to the goal's
  dial (`strict` ⇒ eligible, `trust` ⇒ owner-merge) is cleaner but moves a
  deploy-scope decision into goal scope — the exact thing `merge.py`'s docstring
  argues against.
- **[OPEN] O3 — Where does the owner ledger live?** Extend the ADR 0006 cycle
  report vs a separate "deliveries awaiting you" notification vs console-only
  (P2 of v1-helper). One home, not three.
- **[OPEN] O4 — Goal-less direct tasks: merge policy?** `dispatch_task` deliveries
  currently never consider automerge. Same policy as goals (resolved per-project)?
  Or always `left-for-owner` since no done-gate safety net exists on that path?
- **[OPEN] O5 — The checklist-mode skip's legibility.** Keeping the pillar-2 skip
  is right (shared goal-branch PR); should it log/notify `skipped(checklist-mode)`
  so an automerge-on owner isn't left inferring silence?

## 6. Explicitly not proposed

- No new gate, no weakening of any existing gate (verify/integrity/done stay
  always-hard; fail-closed semantics untouched).
- No LLM call on any tick path — everything here is mechanism + projection.
- No goal-level `automerge` field (the 2026-07-05 lesson in `merge.py` stands
  unless O2 explicitly reopens it).
