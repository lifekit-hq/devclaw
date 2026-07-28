# Proposal — delivery last-mile: the "PR merges, I get told, the goal pursues" contract

- **Status:** **LOCKED (direction)** — 2026-07-28, same-day clarify: all 5 `[OPEN]`s
  resolved by Denys in conversation, recorded in §5. Locking commits direction only;
  tranche scheduling stays Denys's call.
- **⚠ Invariant amendment (headline, per spec-lifecycle):** the O1 resolution
  **amends [ADR 0007](../decisions/0007-gate-strictness-dial.md)'s backstop premise
  for automerge-on repos**: under `trust`, a gate-green delivery auto-merges *even
  when advisory findings survived*. The pre-merge human backstop is replaced by the
  done-gate review + the owner ledger (findings surfaced loudly *after* landing, §3.3).
  On automerge-off repos the ADR 0007 premise stands unchanged.
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
2. **Merge policy is dial-consistent without merge-time dial logic** (resolved
   O1+O2): gate-green ⇒ merge, on any repo whose ops-scope switch is on. Under
   `strict`, surviving findings already fail closed upstream, so every delivered
   PR is clean by construction; under `trust`, advisory findings ship *and merge*
   — the done-gate is the safety net, and the findings ride the owner ledger
   (§3.3) so they are seen loudly after landing rather than gating before it.
   See the invariant-amendment headline above.
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

- **P1 (firmed at lock):** total merge-policy resolution (`merged |
  left-for-owner(reason) | skipped(reason)`, per §5 O1/O2/O4/O5) + the PR ledger
  on the cycle report (O3). Composition + projection; no new gates, no tick-path
  LLM. Sizing: ~2 PRs (policy resolution + ledger), end-of-week cap.
- ~~P2 strictness-aware merge policy~~ — dissolved by the O1+O2 resolution (no
  merge-time dial logic exists to build).
- Out of scope here: #393/#394 (independent mechanical fixes), console file-a-task
  + direct-task visibility (v1-helper P2), work-item provenance
  (issue-driven-pipelines).

## 5. `[OPEN]` items — RESOLVED (clarify step, Denys in conversation, 2026-07-28)

- **O1 — What does "merge" mean under `trust`? → Auto-merge anyway.** The
  hands-off decision-2 vision wins: gate-green merges even with surviving
  advisory findings; the done-gate is the safety net and the findings surface
  loudly in the owner ledger post-merge. This is the invariant amendment in the
  headline above. (Denys chose this over the recommended left-for-owner.)
- **O2 — Where does the switch live? → Keep ops-scope, no goal-level field.**
  `DEVCLAW_GOAL_AUTOMERGE` + the per-project override remain THE eligibility
  switch. With O1, no merge-time strictness logic is needed at all: `strict`
  already fails findings closed upstream, so the dial does its work at the gate,
  not at merge. The 2026-07-05 no-goal-field lesson stands.
- **O3 — Owner-ledger home? → Extend the ADR 0006 cycle report.** One home: the
  existing cycle-close notification gains a PR-ledger section (merged w/
  one-liners; awaiting-owner w/ reason incl. surviving findings; goal-less
  direct-task deliveries). No second notification stream.
- **O4 — Goal-less direct tasks? → Always `left-for-owner`.** The ADR 0011 path
  never auto-merges: no done-gate net exists behind it, and caller-pinned target
  branches are where a surprise merge hurts most. Its deliveries appear in the
  ledger instead.
- **O5 — Checklist-mode skip legibility? → Yes, log it.** The pillar-2 skip
  stays, but resolves as an explicit `skipped(checklist-mode)` in the goal log
  (and is visible via the ledger) so an automerge-on owner never infers from
  silence. (Resolved as the stated default; flagged for Denys's veto.)

## 6. Prior art — wheelhouse's auto-merge gate (steals for the P1 tranche)

*Added 2026-07-28, post-lock. Detail-level input for the P1 tranche only — it
does not reopen any §5 resolution. Source: teardown of
[kunchenguid/wheelhouse](https://github.com/kunchenguid/wheelhouse) (vault:
`projects/devclaw/wheelhouse-teardown-2026-07-28.md`), whose scan-time
auto-merge is the most elaborated fail-closed version of exactly the O1
contract: unattended merge with a post-hoc human safety net.*

**Adopt into P1 (mechanics, no policy change):**

- **Head-bound verdict.** Wheelhouse binds its merge-authorizing verdict to the
  exact (head SHA, base, vision revision) and refuses any stale binding. P1
  equivalent: `merged` may only resolve for the **exact head SHA the gates
  passed**; if the PR head moved after settle, resolve
  `left-for-owner(head-moved)`. This is #393's freshness class applied at the
  merge boundary.
- **Live re-read immediately before the merge call.** Wheelhouse re-reads head,
  base, mergeability, checks, escape label, and card activity right before its
  (already-authorized) merge, and any uncertainty leaves the PR for normal
  review. P1 equivalent: between gate-green and `gh pr merge`, one cheap
  re-read of head / mergeability / owner activity; **any surprise or read
  failure resolves `left-for-owner(reason)`, never a retry loop and never a
  silent skip**. Fail-closed at the last inch, consistent with §1's
  CONFLICTING-PR morning.
- **Owner activity wins.** Wheelhouse lets a maintainer action taken during the
  gate window beat the machine's merge. P1 equivalent: owner comment / review /
  push on the PR after delivery ⇒ `left-for-owner(owner-active)`. The human
  outranks the loop without touching any switch.
- **Per-PR escape hatch.** `wheelhouse:no-auto-merge` on a target PR stops one
  pending automatic merge without disabling the feature. P1 equivalent: a
  `devclaw:no-auto-merge` label checked in the pre-merge re-read ⇒
  `left-for-owner(owner-hold)`. Today the only opt-out is repo-wide
  (`Project.automerge`); this is the missing one-PR granularity, mechanical and
  zero-LLM. *(New affordance — flagged for Denys's veto, same footing as O5.)*
- **Merge evidence is durable.** Every wheelhouse auto-merge appends its
  qualifying evidence to a closed ledger issue. P1 equivalent: the delivery
  record behind the O3 ledger stores *why* the merge was authorized (gate
  verdicts, head SHA, resolution enum value + reason) — the ledger line is a
  projection of stored evidence, not a log grep.
- **Never auto-revert.** Convergent with the locked O1 posture (done-gate +
  ledger are the net, not rollback); named here so the tranche doesn't invent
  one.

**Declined (with reasons — they compensate for weaknesses devclaw doesn't have):**

- **Size caps (≤20 files / ≤1,000 lines) and sensitive-path exclusion lists.**
  Wheelhouse merges *other people's* PRs on the strength of a lightweight
  advisory triage, so it bounds blast radius by diff shape. devclaw merges its
  **own** deliveries after the full always-hard verify/integrity/done stack has
  read the entire diff; re-bounding by size would second-guess the gates and
  contradict the locked "gate-green ⇒ merge" totality (§5 O1/O2).
- **VISION.md-alignment + A/B/C behavior-class verdict.** That's wheelhouse
  rebuilding, at merge time, the intent-conformance check devclaw already runs
  as the firmed `done_when` + evaluator. No merge-time cognition — §7 stands.

## 7. Explicitly not proposed

- No new gate, no weakening of any existing gate (verify/integrity/done stay
  always-hard; fail-closed semantics untouched).
- No LLM call on any tick path — everything here is mechanism + projection.
- No goal-level `automerge` field (the 2026-07-05 lesson in `merge.py` stands
  unless O2 explicitly reopens it).
