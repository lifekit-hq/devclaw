# Proposal — the compounding experiment: does a durable goal build on itself across nights?

- **Status:** **P1 LOCKED (direction)** — 2026-08-07. Captured + clarified + locked
  in one direction-scoping conversation the same evening the console-legibility
  thread closed (#482 merged). Four defining answers locked in-conversation:
  **success = monotonic progress → close** (not a head-to-head-vs-one-shot
  baseline); **the goal = a real .NET + Angular app** in Denys's stack; **the
  grading checklist is scorecard-side and hidden from the worker** (the worker
  builds toward a prose `SPEC.md`, the gate/scorecard verify against unseen
  executable checks — trust-the-input / verify-the-output); and the P1 budget +
  §6 mechanical defaults confirmed. Locking commits **direction only** — the
  nightly *runs* are Denys's call on the live box; a locked line stays reopenable
  (edit here, don't silently diverge).
- **Date opened:** 2026-08-07 · **Authors:** Denys + Claude
- **Relates to / does not restate:** the invariants in [`CLAUDE.md`](../../CLAUDE.md)
  and the **grounded done-gate** ("Done is a proposal, gated on grounded
  evaluation") — this experiment *depends on* that gate being real: the app's
  `done_when` IS the executable checklist below, and the goal must close only when
  the evaluator confirms it against that checklist. Builds on the eval projection
  ([ADR 0006](../decisions/0006-continuous-eval-projection.md)) — the compounding
  scorecard is a *new* per-night projection alongside `eval_outcomes`, not a
  replacement. Instrument precedent: `evals/measure_goal_loop.py` (on branch
  `docs/demolition-artifacts-2026-08-05`) and the dormant `measure_passrate.py`.

## The problem — the thesis is asserted, never observed

devclaw's differentiator, per the 2026-08-07 relevance audit, is the **durable-goal
layer + grounded done-gate** wrapped around a worker (OpenHands V1 SDK) that
frontier models already match on *single-task* execution. The whole bet is that
pursuing one durable goal **across many nights compounds** — night N+1 builds on
the merged output of nights 1..N — in a way a one-shot frontier agent does not.

That has **never been observed.** The closest data point (the 2026-08-05 scale
test: a mini-YouTube built, tested, auto-closed with no per-tick planner) was
explicitly **one-shot** — "multi-session compounding not yet observed" (session
memory). Every disappointment in the arc ("it's a toy, can't trust it at scale")
reduces to this unmeasured claim. If compounding is real, devclaw does something
a one-shot agent can't. If each night restarts / duplicates / reverts, devclaw is
a scheduler around a one-shot — and the doubt is *confirmed*. **Both outcomes are
worth the experiment; a null result is a real result.**

## The claim, stated so it can fail

> A durable goal whose `done_when` needs ~5–10 nights of work (un-oneshottable)
> will, run on the live box night after night, show a **monotonically
> non-decreasing** count of satisfied `done_when` criteria, **never reverting** a
> previously-satisfied criterion, and will **eventually close** via the grounded
> done-gate — reaching an end-state a single session provably could not.

Falsified by any of: **plateau** (criteria-count flat for K consecutive working
nights while nights still burn tokens), **churn** (a criterion flips
satisfied→unsatisfied — night K undoes night K-1), or **restart** (night K's diff
re-implements criteria already green — duplication, not compounding).

## The metric — an executable checklist, run each night

The move that makes this mechanical, not a judgment call: **encode the app's
acceptance as an executable checklist — one deterministic check per feature —
held scorecard-side, hidden from the worker.** The worker is briefed with a
prose `SPEC.md` (what to build); the checklist (how we verify) never enters the
target repo. This is devclaw's own **trust-the-input / verify-the-output**
principle applied to the experiment: a "green" means the feature genuinely
works, not that the worker satisfied an assertion it could see and game (the
#358 route-around-constraints lesson). Then the metric is trivial and honest:

1. `evals/ledger_checklist/` (in the devclaw repo, **not** the target repo) —
   N independent checks, each exiting 0/1: *does this feature work against the
   current repo state?* (build passes, endpoint returns X, test file green,
   Playwright smoke renders Y).
2. After each night's merges, the **compounding scorecard** clones the target
   repo at HEAD, runs the full hidden checklist, and records the night's
   **criteria vector** (an N-bit green/red snapshot) + tokens spent that night.
3. **Compounding** = the green-count is non-decreasing across working nights AND
   reaches N (→ the done-gate closes the goal). **Anti-signals** = any green→red
   flip (churn) or a flat green-count across K working nights (plateau).

The grounded done-gate (a *control-plane* evaluator, not the worker) grades the
goal's `done_when` against this same hidden checklist — so the gate and the
scorecard measure one thing, while the worker only ever sees the prose spec. No
drift between "what closes the goal" and "what we're grading"; no leak of the
grader to the graded.

## The app — "Ledger", a personal expense tracker (.NET minimal API + Angular)

Concrete, in Denys's stack, layered so compounding is *visible* (each criterion
mostly builds on the prior), and every criterion has an objective check. Fresh
repo (no prior-run contamination). Draft `done_when` checklist (~10 criteria):

| # | Criterion | Objective check |
|---|---|---|
| 1 | Backend scaffold builds | `dotnet build` green; `GET /health` → 200 |
| 2 | Persistence | EF Core + SQLite; `Expense` entity; migration applies clean |
| 3 | Expense CRUD | POST/GET/PUT/DELETE `/expenses` + validation; integration tests green |
| 4 | Categories | `Category` entity + `/categories`; an expense references a category |
| 5 | Filtering | `GET /expenses?category=&from=&to=` returns the filtered set; tested |
| 6 | Summary | `GET /expenses/summary` → totals by category & month; tested |
| 7 | Auth | JWT login; `/expenses` scoped to the authenticated user; unauth → 401 |
| 8 | Frontend scaffold | Angular app builds; lists expenses from the live API |
| 9 | Frontend CRUD | add / edit / delete an expense in the UI, with a category dropdown + filters |
| 10 | Summary view | totals chart/table renders; `ng build` green; Playwright smoke passes |

`done_when` = all 10 checks green **and** the full test suite passes. The worker
sees these as **prose features in `SPEC.md`** (committed to the target repo); the
exact assertions in the "objective check" column live scorecard-side in
`evals/ledger_checklist/` and never enter the target repo. Un-oneshottable
(backend + persistence + auth + frontend + e2e across 10 features); every criterion
is independently, mechanically verifiable.

## P1 — the smallest thing that ships (from *this* box)

Per spec-lifecycle "slice, don't estimate": P1 is **the design + the instrument**,
not the run. Sized in devclaw units, end-of-week cap.

- **P1-A — this proposal, LOCKED** (§6 opens resolved). *(this session)*
- **P1-B — the Ledger `SPEC.md` (prose, worker-facing) + the hidden executable
  checklist** (`evals/ledger_checklist/`, scorecard-side, in the devclaw repo).
  Two artifacts, deliberately separated: the worker only ever gets the prose;
  the checks are the grader. ~1 PR, testable dry (each check runs green against a
  reference "done" fixture and red against an empty one).
- **P1-C — the compounding scorecard**: a runner that clones the target repo at
  HEAD, runs the hidden checklist, appends the criteria vector to a
  `compounding_runs` projection, and renders a small nightly delta report
  (green-count, new-green, any churn/green→red). Reuses the `measure_goal_loop.py`
  shape; stub-testable. ~1 PR.
- **Then (Denys, live box):** create the repo, file the durable goal with the
  checklist as `done_when`, let it run nightly, read the scorecard each morning.

P2/P3 (named, unsized): a console "Compounding" view over the projection; a second
app to test generality; the head-to-head-vs-one-shot contrast (see [OPEN-5]).

## 6. Clarify — resolved (2026-08-07)

- **[OPEN-1] Churn/revert detection — RESOLVED.** The checklist is ground truth:
  a criterion flipping green→red between two nights is logged as a churn event and
  breaks the "never reverts" claim. No separate diff-overlap heuristic in P1
  (add only if plateau/churn later needs finer attribution).
- **[OPEN-2] Criteria that resist a pure exit-code (UI) — RESOLVED.** Criteria
  8–10 use a scripted `ng build` + Playwright smoke as their check; if a smoke is
  flaky, that criterion falls back to the grounded evaluator's judgment logged
  explicitly as "judged, not executed" — never silently.
- **[OPEN-3] P1 budget — RESOLVED.** P1-B + P1-C land this week (~2 PRs from the
  box); the nightly run is Denys's, target ~5–10 nights before we call the result
  either way.
- **[OPEN-4] Where the checklist lives — RESOLVED: scorecard-side, hidden.** The
  worker gets a prose `SPEC.md` in the target repo; the executable checks live in
  the **devclaw repo** (`evals/ledger_checklist/`) and never enter the target
  repo — trust-the-input / verify-the-output. A green means the feature works,
  not that a visible assertion was satisfied (#358).
- **[OPEN-5] Counterfactual — DEFERRED to P2 (owner: Denys).** Success is defined
  as monotonic-progress-to-close alone (locked), so **no one-shot baseline in
  P1**. The head-to-head contrast ("a single best-effort agent gets provably less
  far") is a **P2** strengthener, not a P1 gate. Reopen if the P1 null/positive
  result needs a comparator to be believed.

## Amendment 2026-08-08 — substrate gap: reset-to-main amnesia → goal-branch accumulation for long_lived

**The night-1 finding.** Before the first real nightly run could compound, the
substrate silently erased it. A long_lived durable goal runs one "advance one
increment" task per night, and `engine/workspace.py::prepare_workspace` resets the
task's workspace (`fetch → reset --hard origin/main → clean`) **before every
task** — *unless* a goal branch is in play. Long_lived thin-advance passed **no**
goal branch → reset to `origin/main`; and because a long_lived goal's single PR is
**not** merged, `main` stays empty → the reset **wiped the prior night's work** →
the worker rebuilt from scratch every night (observed: two from-scratch builds in
different stacks). That reset-to-main step silently **assumes the PR merged**;
for unmerged long_lived accumulation that assumption is false, so it is amnesia,
not compounding — the whole reason an early run showed churn, not compounding.

**The fix (this PR).** `devclaw/goal/delivery_strategy.py::resolve_strategy` now
resolves a **long_lived goal in the `executing` lifecycle** to the `GOAL_BRANCH`
strategy — the same battle-tested machinery checklist goals already use (Pillar
1/2, #394): a `goal/<id>` branch created off main on night 1, then `fetch → reset
--hard origin/goal/<id>` each night, **preserving the accumulated work**. One
predicate change; it flows automatically to the dispatch target-branch and the
three done-gate branch reads (which now review the goal branch — the accumulated
work — instead of an empty main). Legacy goals (`lifecycle is None`) and one_shot
goals are unchanged. Regression tests: `test_long_lived_goal_accumulates_on_goal_branch`
(dispatch carries `target_branch=goal/<id>`) and the `resolve_strategy` unit tests
(`test_long_lived_executing_no_checklist_resolves_goal_branch`, plus the negatives
for legacy/pre-executing/one_shot).

**Consequence for the scorecard (operator instruction — supersedes "clones the
target repo at HEAD" above).** Accumulation now lives on `goal/<id>`, **not** on
`main` (which stays empty until a slice is deliberately landed). So the nightly
compounding scorecard (P1-C) must clone/grade **the `goal/<id>` branch** (or the
reused single PR's head), **NOT** `main`. `evals/compounding.py` already takes a
repo + ref, so this is an operator-instruction note, not necessarily a code
change: point the scorecard's ref at `goal/<goal_id>`. Grading `main` HEAD would
read all-red every night and falsely report a total plateau.

**Deliberately deferred (named fast-follow — do NOT guess).** *Bounding /
landing verified slices on main* is a **separate** PR. This change keeps the
long_lived goal's single PR to main **open and reused**, never auto-merged by the
strategy resolution. Two reconciliations were left untouched on purpose, because
auto-merge is entangled with the `#394/#430` settle-time totality and defaults
**off** (`AUTOMERGE_ENABLED=False`), so the experiment is safe as-is:
1. **Auto-merge skip at settle** (`tick_settle.py`): the "skip auto-merge for a
   shared goal-branch PR" guard keys off `bool(addresses)` (checklist items),
   which is **False** for a long_lived thin-advance dispatch. With auto-merge
   *enabled* on the target repo, a long_lived `goal/<id>` PR would therefore
   auto-merge to main and delete the branch — landing unbounded nightly work on
   main without the intended bounding review. **The experiment must keep
   auto-merge OFF (the default)** until the bounding fast-follow reconciles this
   signal from `bool(addresses)` to "delivery is on a goal branch".
2. **The `#430` `open_unmerged_pr` marker** is still set for a long_lived
   goal-branch delivery (same `bool(addresses)` signal), but it is **never
   consumed** for a goal-branch goal (the done-gate wrong-ref block requires
   `goal_branch(...) is None`), so it is harmless today — folded into the same
   fast-follow.

## Why this isn't just another eval

The eval projection (ADR 0006) measures **per-task** pass-rate and **per-cycle**
cleanliness — both *within* a night. Neither measures the thing the whole thesis
rides on: **does value accumulate *across* nights toward one durable end?** That
is a new axis, and it's the one Denys has never been able to see. This experiment
is the instrument for it — and its result, positive or null, is the most decision-
relevant fact devclaw could produce right now.
