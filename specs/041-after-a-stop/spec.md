# Feature Specification: After a stop — decisions execute, stops re-check

**Feature Branch**: `feat/after-a-stop`

**Created**: 2026-09-08

**Status**: Draft → US1 implemented 2026-09-08; US2 implemented 2026-09-08 (stacked PR) — amended by spec 045 (2026-09-10): an owner accept_close is not outranked by the evaluator's own `auto-eval` rows; they ride the close as follow-ups.

**Input**: `/devclaw-morning` 2026-09-08 → `/root-cause`. Denys: "get the root cause of each problem, spec the fixes, execute them."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: ran but needed the owner (US1) and stopped when it shouldn't (US2).
- **Number that shows it**: `get_scorecard_metrics(336h).interventions.per_achieved_goal` = **3.462** on 2026-09-08 (decisions 10, resumes 7, steers 7); of the 7 steers, 4 were "already decided — proceed"; of the 10 decisions, 3 were repeats of a recorded decision. `get_loop_health(336h).idle.not_stuck_rate` = **0.4468**; `mechanical:dispatch_cap` 24,718 s + `lost_ref` 11,572 s = 97% of devclaw-caused idle.
- **Cut when**: after two weeks live, a repeated `decide` on one goal, an "already decided" steer, or a `resume_goal` on `dispatch_cap` / a fetch `lost_ref` appears in `interventions.items` — the seam still has a fourth exit and the class is elsewhere.

## Root cause *(root-cause skill, 2026-09-08)*

**One seam, six mechanisms, no policy.** After a stop the loop has three declared exits — self-heal (`mechanical:prep/env/ci`), a typed Problem with a default (spec 031), an owner Decision — and a fourth, undeclared one: wait for a human to type `resume` or to repeat a decision. Six mechanisms sit at that seam in `devclaw/goal/tick.py` / `service.py` / `evaluator.py`: the human-gated kind set, the three heal branches, the Problems seam, the closed-issue first-pass shortcut, the strict structural downgrade, and the budget-restoring unblock shape. None of them reads a Decision as something to *do*.

Class 1 — **an owner Decision is recorded but never executed**:
- `tick.py:746` — work is a settle or an unread steering line; a Decision is neither, so a defaulted `correct` (lks-134, idle 10 h) and a `correct_implementation` (fs-431, 15 h) waited for the 1d cadence.
- `service.py:1500` — `resolve_problem` resets `donegate_rounds=0`, re-arming the closed-issue shortcut at `tick.py:900` ("all referenced issues closed — proposing done without dispatching a worker"); a red rollup then steers `auto-ci` and returns idle (`tick_donegate.py:872`), and the next tick takes the shortcut again: fs-431 ran **8 worker-less rounds** on 2026-09-08 11:35–12:52, one `gh` read and one 🔴 ping each, until a 20 s `gh` timeout parked it `lost_ref`. The owner's correction (the Hangfire fix) never ran.
- `evaluator.py:737` — `strict` downgrades a model `achieved` to `off_track` on structural concerns unconditionally; `accept_close` is `closes_goal=True` (`problems.py:29`) but no code path closes on it: fs-318 / fs-421 / fs-429 cycled decide → gate → downgrade → Problem → decide (3 decisions, 4 steers, 4 review calls; still open). A `decide(cancel)` has no executor either.
- Design decision it comes from: spec 031 made a Decision "a fact the done-gate treats as settled" *per clause* (`resolved_by_decision`) and never said what a decision that is not a clause (accept the gap, correct the work, cancel) does to the loop.

Class 2 — **a stop whose only exit is a human typing resume**:
- `tick.py:139` declares five kinds owner-cleared; two park on re-checkable conditions with no decision to make: `dispatch_cap` (`tick_dispatch.py:83`, a counter with `resume` as its only verb, no Problem raised — fs-554 today after one unreviewable-diff gate crash and one 1800 s ACP idle timeout, both no-retry by design) and the issue-fetch `lost_ref` (`tick.py:855–868`, `tick_donegate.py:363`), which files a `gh` exit -1 timeout under the same kind as a destroyed in-flight ref.
- Design decision: the 2026-07-13 harden tranche made every non-healing block owner-cleared, before spec 031 gave stops a typed Problem with a default and before `mechanical:prep` existed for "the remote can come back".

Stacked instance fixes this retires: the "proceed to close" steer pattern (4 this week), the repeat decide (3), tinyspec `closed-contract-raises-a-problem` (kept — its Problem is right; its trigger no longer fires on a pending decision), spec 020's honest-reason patch on the cap (kept as text, no longer the exit), 5 of 7 resumes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An owner Decision is executed by the next tick (Priority: P1)

The owner resolves a Problem. Whatever the verb, the next tick *does* it: `correct_implementation`, `decide(correct)`, `decide(<text>)` and a defaulted `correct`/`continue` dispatch a worker with the Decision in the brief (they are work, and they take precedence over the closed-issue propose-done shortcut); an **owner** `decide(accept_close)` closes and merges the goal on the same mechanical facts as an achieved close (green CI on the current head, merge-on-close) with **no further evaluation** — the owner is the one party who may accept a gap the evaluator reported; `decide(cancel)` cancels the goal in the same transaction as the Decision. A *defaulted* accept_close is unchanged (under `trust` the gate's next round closes; under `strict` it parks) — a default is not an owner ruling.

**Why this priority**: it is the owner's only job, and today the loop ignored it four times in one day.

**Independent Test**: a resolved Problem, then one tick with `FakeClaude`; the tick dispatches (calls == 0) or closes (calls == 0) or cancels; never idles, never re-proposes done without a worker while a correction is pending.

**Acceptance Scenarios**:

1. **Given** a pointer goal whose issues are all closed and a red CI rollup, **When** the owner records `correct_implementation`, **Then** the next tick dispatches a worker carrying the Decision (no propose-done, no second Problem).
2. **Given** a `strict` goal whose gate downgraded an `achieved` on structural concerns and the owner decided `accept_close`, **When** the next tick runs with green CI, **Then** the goal merges and closes with zero evaluator calls, the close rationale names the Decision, and the structural concerns are logged as follow-ups.
3. **Given** the same but CI pending, **Then** the goal holds `mechanical:ci` and closes when the hold heals — still zero evaluator calls.
4. **Given** the same but CI red, **Then** the failing checks are steered as the next correction and dispatched; after the fix settles green the close proceeds without a gate round.
5. **Given** a timed-out Problem whose default is `correct`, **When** the default fires, **Then** the same tick dispatches (no cadence wait).
6. **Given** `decide(cancel)`, **Then** the goal is `cancelled` with an abandoned convergence row.

### User Story 2 - A stop without a decision never waits for a human (Priority: P2)

Reaching the dispatch cap raises a typed Problem (`continue` — refund the cap and dispatch again / `cancel`), default `continue` after the timebox; a second cap on the same goal with **no delivered increment in between** raises the Problem with no default (explicit decide only) — one bounded self-heal, then a human, the merge-conflict shape. A referenced-issue fetch failure (timeout, network, a transient `gh` exit) is a `mechanical:prep` hold that rechecks on the existing backoff and parks with one ping after the heal cap; `lost_ref` is reserved for a destroyed in-flight ref.

**Why this priority**: 97% of devclaw-caused idle in 14 days is these two kinds; five of the owner's seven resumes.

**Independent Test**: a goal at its cap ticks into a Problem with default `continue`; after the timebox it dispatches with the cap refunded; a second cap with no delivery in between produces a Problem whose timebox never elapses. A fetch error blocks `mechanical:prep`, evaluator calls == 0, and the prep heal path is the exit.

**Acceptance Scenarios**:

1. **Given** `actions_dispatched == cap`, **When** the tick dispatches, **Then** the goal blocks `mechanical:dispatch_cap` carrying a Problem (options `continue` / `cancel`, default `continue`).
2. **Given** that Problem's timebox elapsed, **Then** the default records a `continue` Decision, the cap is refunded, and the next tick dispatches.
3. **Given** a `continue` Decision since the last delivered increment and the cap is hit again, **Then** the Problem has no default; only `decide` moves it.
4. **Given** the issue fetch fails at the dispatch boundary or at the done-gate, **Then** the goal blocks `mechanical:prep` (not `lost_ref`), no cognition is spent, and the prep heal's backoff/cap governs the retry.

### Edge Cases

- An owner `accept_close` followed by a later Decision (e.g. `correct_implementation` on a new Problem): the **latest** current Decision rules; the accept no longer closes without the gate, and the gate's next refusal raises a fresh Problem. The last word wins.
- A closes_goal default under `strict` still parks (Q2 → C of spec 031, unchanged).
- An accepted close whose merge conflicts takes the existing one bounded resolution increment, then merges; other merge failures park `mechanical:merge_failed` and `resume_goal` retries the merge only (existing path).
- A cap Problem on a goal whose every increment failed keeps the honest reason text (spec 020 FR-003) in `what`.
- The heal budget for a fetch hold is the prep budget (5 attempts, 30 min → 6 h backoff); a permanently deleted issue exhausts it in ~13 h and parks with one ping.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `should_plan` is true when a current Decision was made after `last_plan_at` (a Decision is work), in addition to a settle or unread steering.
- **FR-002**: The closed-issue propose-done shortcut fires only when the tick has nothing to dispatch: no unread steering and no pending Decision; otherwise the tick dispatches a worker (issue closure stays an input to the brief).
- **FR-003**: When the latest current Decision is an owner-provenance `closes_goal` option, the tick — before any cadence or work gate — reads the rollup: failing ⇒ the CI correction is steered and returns idle; pending/unknown ⇒ `mechanical:ci` hold; green/none ⇒ merge-on-close and ACHIEVE with a rationale naming the Decision; zero cognition on every branch.
- **FR-004**: A settled-ok advance while such a Decision stands routes to the same close, never to the evaluator.
- **FR-005**: `decide(cancel)` and a defaulted `cancel` fire CANCEL (abandoned convergence) in the Decision's transaction.
- **FR-006**: A defaulted `closes_goal` option keeps spec 031's behaviour (trust ⇒ gate; strict ⇒ park).
- **FR-007**: The dispatch cap raises a Problem (`continue` / `cancel`) in the BLOCK transaction; default `continue`; a repeat cap with no delivered increment since the last `continue` Decision raises it with no default.
- **FR-008**: `decide(continue)` / a defaulted `continue` unblocks with `actions_dispatched=0` and is a pending Decision (FR-001).
- **FR-009**: An `IssueRefError` at the dispatch boundary or the done-gate's live contract blocks `mechanical:prep`; the prep heal is the exit; the notify is TASK-level (the heal's give-up pings OWNER once).
- **FR-010**: `HUMAN_GATED_MECHANICAL_KINDS` keeps `mechanical:lost_ref` (in-flight ref) and `mechanical:dispatch_cap` (declared; now Problem-bearing).

### Key Entities

- **Decision** (existing): `verb`, `option_key`, `text`, `provenance`, `made_at` — "pending" is derived (`made_at` > `last_plan_at`), "accepted close" is derived (latest current Decision is owner `accept_close`). No new columns.
- **ProblemOption** `CONTINUE` (new): key `continue`.

## Success Criteria *(mandatory)*

- **SC-001**: `interventions.steers` carrying "already decided" and repeat `decide`s on one Problem chain: 0 over the next 14 days (baseline 4 + 3 this week).
- **SC-002**: `resume` verbs on `dispatch_cap` or a fetch `lost_ref`: 0 over 14 days (baseline 5 of 7).
- **SC-003**: `not_stuck_rate` (336h) ≥ 0.85 (baseline 0.4468).
- **SC-004**: fs-431's recorded correction dispatches on the first tick after deploy; fs-318/421/429 close on their recorded `accept_close` with zero evaluator calls.

## Assumptions

- The evaluator prompt is untouched; the owner's accept bypasses it by code, not by instruction.
- Constitution V is amended in this PR: the owner's explicit `accept_close` closes on mechanical facts with no further evaluation (one sentence; the evaluator remains the only *machine* ACHIEVE emitter).

## Rejected alternatives

- **Re-run the gate once with the accept honoured** (asked 2026-09-08): one evaluator call per close for no new fact; the owner's accept is already the verdict. Rejected by Denys.
- **Keep strict as is; accept works only under trust**: leaves the decision inert on the goals the owner most cares about — today's defect.
- **Cap Problem default = cancel**: a transient (529, idle timeout) would kill a goal overnight.
- **Cap Problem with no default**: turns a resume into a decide but still waits for the owner every time — the number does not move.
- **A new `mechanical:ref` kind with its own heal**: a third mechanism at the boundary; `mechanical:prep` already means "the remote can come back" and carries the backoff.
- **A `pending_decision` column**: persisted state + a doctor check for a fact derivable from `goal_decisions.made_at` and `last_plan_at`.
- **Patch the instances** (steer fs-431 by hand, flip fs-318 to trust): the class survives; the next goal repeats it.

## Clarifications

### Session 2026-09-08

- Q: On an owner `accept_close` under strict, what does the next tick do? → A: close without re-evaluating (the decision is the verdict; CI green + merge-on-close; structural concerns as follow-ups).
- Q: What replaces the human-gated dispatch cap? → A: a Problem with default `continue` once; a second cap with no delivered increment in between parks for an explicit decide.
