# Feature Specification: The change is the worker's own

**Feature Branch**: `045-the-change-is-the-workers-own`

**Created**: 2026-09-10

**Status**: Specified and clarified with Denys 2026-09-10 (one ruling, below) from the fs-431 root-cause read (13 blocks, 5 owner verbs, 2026-09-02 → 2026-09-10); implemented in full in the same PR — US1 (span filter), US2 (`conflicting` state + one conflict routing + resume refund), US3 (accept_close outranks `auto-eval` rows).

**Input**: User description: "The change is the worker's own. Three stops on fs-431 (2026-09-09/10) share one shape: a fact the loop already holds is not the fact the deciding seam reads. (1) The judged span is the tree delta pre_run_sha..post_run_sha, so a merge-conflict resolution increment, which must merge the default branch, is charged with every gate-input file main moved and fails change_class closed; the spec 025 conflict heal is structurally impossible on a repo with dependabot on actions. (2) The CI hold treats a required check that has not reported as pending; GitHub never runs pull_request workflows on a CONFLICTING PR, so the hold waits 16 windows for checks that cannot exist and parks for the owner, while the settle path had already read CONFLICTING two seconds earlier and only logged it. (3) The owner's accept_close is outranked by the evaluator's own unread structural-concern steering rows, the exact gap the accept accepted, so a 3h worker session runs before the close. Fix at the root: the span excludes what the base branch already carries; a CONFLICTING read is the conflict outcome, never a wait; an owner accept_close consumes machine concern rows as the accepted gap; a human resume refunds the conflict heal like every other heal budget."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **ran but needed the owner**, primarily; **stopped when it
  shouldn't**, secondarily. Every stop below ended in an owner verb that was
  not a decision, or in a ping asking for one (a hand rebase, a resume), and
  the goal sat idle for hours first.
- **Number that shows it**: `get_loop_health`, 72 h read of 2026-09-10 05:00
  UTC: devclaw-caused idle **51,098 s of 203,558 s observed** (not-stuck rate
  **0.749**); on fs-431 alone `lost_ref` 12,475 s, `mechanical:ci` 6,131 s,
  `donegate_churn` 1,133 s. `get_goal fs-431-hygiene-sentinels-2026-08-31`:
  **13** blocks and **5** owner verbs in 8 days, the last of them the current
  park — "auto-recovery gave up after 16 attempts — the delivered PR's CI
  never settled; needs you" on a PR GitHub reports `CONFLICTING`, whose
  checks cannot run. Two conflict-resolution increments failed `change_class`
  on this goal (2026-09-08: five workflow files; 2026-09-09 23:30:
  `.github/workflows/docker-build.yml`) — both were main's own edits.
- **Cut when**: US1 is cut if, 14 days after it lands, no increment's span
  dropped a base-branch path (the filter fired on nothing). US2 is cut if in
  the same window no close met a CONFLICTING PR (the state was never read).
  US3 is cut if no owner `accept_close` met an unread machine concern row.
  Each story that fires on nothing is a mechanism nobody needed.

## The root, named once

One shape, three seams: **a fact the loop already holds is not the fact the
deciding seam reads.**

1. The loop holds the base branch (it clones from it, delivers to it) and
   judges the worker on a range that includes what the base branch moved.
2. The loop reads `CONFLICTING` at settle and holds on "pending" at close,
   waiting for checks GitHub will never create for a PR it cannot merge.
3. The loop records the owner's `accept_close` as the verdict and then
   dispatches on the evaluator's own concern rows — the gap that verdict
   accepted.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The judged span is the worker's own (Priority: P1)

A conflict-resolution increment merges the default branch into the goal
branch. The gates, the change-size projection, the advisory checks and
delivery judge only what the worker authored: paths the base branch already
carries in that content are not the worker's change. A workflow file
dependabot bumped on main last week is main's edit, not a gate-input edit
"to make a gate pass".

**Why this priority**: it is the stop holding fs-431 now, and it makes spec
025's ONE bounded conflict heal structurally impossible on every repo whose
default branch moves a gate input between fork and close — which, with
dependabot on actions, is the steady state, not an edge. Until this lands the
conflict heal cannot succeed, so US2's routing would only park faster.

**Independent Test**: in a repository with a remote default branch that has
moved a workflow file since the goal branch forked, a worker run that merges
the default branch and edits one product file settles `done` with
`gate_input_paths` empty, a diff that shows the product file only, and a
change-size of one file. The same run with the worker ALSO editing a
workflow file the base does not carry fails `change_class` closed, naming
that path only.

**Acceptance Scenarios**:

1. **Given** a goal checkout whose base branch carries a changed
   `.github/workflows/*` file since the fork, **When** the worker merges the
   base branch and changes one product file, **Then** the task settles `done`,
   the span names the product file only, and `change_class` passes.
2. **Given** the same checkout, **When** the worker merges the base branch
   AND edits a workflow file to content the base does not carry, **Then**
   `change_class` fails closed naming only that workflow path.
3. **Given** a checkout with no resolvable base ref (no remote), **When** a
   run ends, **Then** the span is the unfiltered range exactly as today and
   the change carries the note that no base was read — judging more, never
   less.
4. **Given** a worker that rebases instead of merging, **When** the run ends,
   **Then** the span is the same set as scenario 1 (the definition does not
   depend on the merge shape).

---

### User Story 2 - A conflicting PR is a conflict, never a wait (Priority: P2)

At every seam that reads the delivered PR's CI before a close — the gate
open, the owner's accepted close, the merge — a PR GitHub reports
`CONFLICTING` is routed to the conflict outcome spec 025 already defines: the
one bounded resolution increment when it is unspent, otherwise the
`mechanical:merge_failed` park with its typed Problem. It never enters the
`mechanical:ci` hold, and a hold already in place lifts on the first
conflicting read. A human resume of a merge-parked goal refunds the conflict
heal, the way it already refunds the CI, prep and env heals.

**Why this priority**: the 4 h `mechanical:ci` wait on fs-431 (16 rechecks on
three required checks that cannot exist for a conflicting PR) ended in a ping
for the owner; the loop had read `CONFLICTING` at settle two seconds before
entering the hold and only logged it. A brake that cannot observe its own
release condition is the defect the constitution already names for the env
hold.

**Independent Test**: with the CI reader returning "required checks
unreported" for a PR whose mergeability is `CONFLICTING`, an accepted close
(and a gate open) leaves the goal idle with the `[merge-conflict]` steering
row and `merge_heal_attempted` set — no `mechanical:ci` block, no recheck
line — and, with the heal already spent, parks `mechanical:merge_failed`
with its Problem on the same tick.

**Acceptance Scenarios**:

1. **Given** an owner `accept_close` standing and a PR whose rollup is
   unreported and whose mergeability is `CONFLICTING`, **When** the tick
   runs, **Then** the goal returns to idle carrying the conflict steering
   row and the heal marked spent; no `mechanical:ci` block is written.
2. **Given** the same PR with the conflict heal already spent, **When** the
   tick runs, **Then** the goal parks `mechanical:merge_failed` with the
   spec 025 Problem — on that tick, not after 16 windows.
3. **Given** a goal already held on `mechanical:ci`, **When** the recheck
   reads `CONFLICTING`, **Then** the hold lifts on that read and the next
   tick routes the conflict as in 1 or 2.
4. **Given** a goal parked `mechanical:merge_failed`, **When** the owner
   resumes it, **Then** the merge is retried (spec 025 FR-018 unchanged) and,
   if it conflicts again, the ONE bounded heal is available again — the
   resume refunded it.
5. **Given** a rollup that is green on a `CONFLICTING` PR (push-triggered
   workflows), **When** any close seam reads it, **Then** conflicting wins:
   the close does not proceed to a merge that would fail.
6. **Given** GitHub still computing mergeability (`UNKNOWN`), **When** the
   reader returns, **Then** the state is whatever the rollup says (pending
   stays pending) and the next read resolves it.

---

### User Story 3 - The owner's accept_close outranks the machine's concerns (Priority: P3)

An owner `accept_close` is executed on the next tick as a close. Unread
steering rows the evaluator wrote (the structural concerns the owner just
accepted as the gap) do not dispatch a worker first: the close consumes them
and records them as the accepted follow-ups. Rows from a human, and
mechanical corrections (a red-CI correction, the merge-conflict resolution
row), still dispatch first — a human's later word and a mechanical fact
outrank the accept, exactly as spec 041 rules.

**Why this priority**: on 2026-09-09 the owner's `accept_close` at 18:55
dispatched a 3 h worker session (18:55 → 21:42) on eight unread evaluator
concern rows before the close ran at 21:42, by which time main had moved
again. Spec 041 FR-003's "closes NOW" held only when the machine had left
nothing unread — and after a strict downgrade the machine always has.

**Independent Test**: with an owner `accept_close` recorded and only
`auto-eval` rows unread, one tick closes and merges with zero dispatches and
zero evaluator calls, the rows consumed, the close rationale naming them as
follow-ups. With one unread human row, the same tick dispatches the worker
first.

**Acceptance Scenarios**:

1. **Given** an owner `accept_close` and unread `auto-eval` steering rows,
   **When** the tick runs, **Then** the goal closes on that tick with no
   dispatch, the rows are consumed, and each is logged as an accepted
   follow-up.
2. **Given** an owner `accept_close` and one unread human steering row,
   **When** the tick runs, **Then** a worker dispatches first and the accept
   stands for the close when it settles (spec 041 unchanged).
3. **Given** an owner `accept_close` and an unread `auto-ci` red-CI
   correction, **When** the tick runs, **Then** the correction dispatches
   first (a red CI is a fact the close must not skip).

---

### Edge Cases

- The worker resolves a conflict in a gate-input file with content that
  differs from the base branch: that IS a worker edit and `change_class`
  fails it closed. The merge-conflict steering row therefore says: for a
  gate-input file, take the base branch's side.
- The base ref resolves but `merge-base` fails (shallow clone, unrelated
  histories): the span is unfiltered, as in US1 scenario 3.
- A worker's earlier increment edited a file and this increment reverts it
  to the base's content: the path leaves the span. Nothing shipped differs
  from the base at that path; the diff text and the change-size agree.
- The settle-time mergeability probe still grounds the next brief
  (`pr_state … mergeable=CONFLICTING`) and still logs; its OWNER-level ping
  ("needs a rebase or hand-resolution") is retired — the close owns the
  conflict now, and the ping asked for the very owner action this spec
  removes.
- `accept_close` while a `[merge-conflict]` row is unread: the resolution
  increment dispatches first (it is a mechanical correction), then the
  accept closes on settle.
- A *defaulted* accept never closes without the gate (spec 041 unchanged);
  US3 concerns owner-provenance accepts only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The judged span — its paths AND its rendered diff — MUST
  exclude every path whose post-run content equals the base branch's content
  at the merge-base of the base branch and the post-run head, when the base
  ref resolves in the checkout. The base ref is the delivery base: the
  caller-chosen `base_branch` when set, else the remote default branch.
- **FR-002**: The span keeps ONE definition (spec 013): the filter lives
  where the span is captured, and no gate, projection, advisory or delivery
  path re-derives it.
- **FR-003**: When no base ref resolves, or the merge-base cannot be read,
  the span MUST be the unfiltered range and the change MUST carry a
  one-line note saying so. Failing to filter judges more, never less; it is
  never silent.
- **FR-004**: The CI reader MUST report the PR's mergeability with the
  rollup, from the same read (no additional subprocess), as a distinct
  state `conflicting` that never counts as pending, passing, or a head to
  pin.
- **FR-005**: Every seam that consults the CI reader before a close (the
  gate open, the accepted close, the merge-time hold, the CI auto-heal)
  MUST route `conflicting` to the conflict outcome of spec 025 FR-017/018:
  the one bounded resolution increment when unspent, else the
  `mechanical:merge_failed` park with its typed Problem. The
  `mechanical:ci` hold is never entered on `conflicting`, and an existing
  hold lifts on the first `conflicting` read.
- **FR-006**: The conflict outcome has ONE routing, shared by the merge
  attempt's `CONFLICT` result and the reader's `conflicting` state.
- **FR-007**: A human `resume_goal` MUST refund the conflict heal
  (`merge_heal_attempted`), as it already refunds the CI, prep and env heal
  budgets; `steer_goal` already does.
- **FR-008**: The `[merge-conflict]` steering row MUST tell the worker to
  take the base branch's side for any gate-input file.
- **FR-009**: The settle-time mergeability probe keeps its log line and its
  brief grounding; its owner ping is retired.
- **FR-010**: An owner-provenance `accept_close` with only machine
  evaluation rows (`auto-eval`) unread MUST close on that tick: the rows are
  consumed by the close transition and each is recorded as an accepted
  follow-up. Unread rows from a human or from a mechanical correction
  (`auto-ci`, `auto-conflict`) dispatch first, unchanged.
- **FR-011**: None of the above adds a cognition call or a tick-path
  subprocess on an idle or blocked goal (constitution III).

### Key Entities

- **ChangeSet**: the one answer to "what did the agent change" — gains the
  base ref it was filtered against and the note when it was not.
- **RemoteChecksResult**: the CI fact for a PR head — gains the state
  `conflicting`.
- **Steering row**: `source` distinguishes a human line, a mechanical
  correction (`auto-ci`, `auto-conflict`) and a machine evaluation
  (`auto-eval`); US3 reads that column, adds no new one.
- **Decision**: unchanged (spec 041).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: fs-431 closes through its own conflict heal after ONE
  `resume_goal` (the exit of the park the old code made), with zero
  evaluator calls and no other owner verb — the resolution increment lands,
  CI runs on the merged head, the accepted close merges.
- **SC-002**: A conflict-resolution increment that merges a default branch
  carrying gate-input changes passes `change_class` (the class test in
  `tests/test_materialize_gate.py` gains the case).
- **SC-003**: In the 14 days after deploy, zero `mechanical:ci` give-ups
  ("CI never settled") on a PR that was `CONFLICTING` at the time —
  `list_problems` shows none.
- **SC-004**: An owner `accept_close` with only machine concern rows unread
  closes on the same tick with zero dispatches (the class test in
  `tests/test_merge_on_close.py` gains the case).
- **SC-005**: `get_loop_health` devclaw-caused idle attributed to
  `mechanical:ci` on conflicting heads reads 0 s over the window.

## Rejected alternatives (direction memory)

- **Hand-rebase PR 606 and resume** — an owner action; it is the defect,
  not the fix. Done ONCE at most as a labelled instance fix if the loop's
  own heal fails after this lands.
- **Let the conflict increment declare the workflow paths in scope** (spec
  032 FR-008) — patches the gate and leaves the review, the change-size and
  the advisory reads judging main's code as the worker's.
- **Filter by commit authorship** — devclaw commits as devclaw and the
  materialize step amends; authorship is not what the worker changed.
- **Judge the whole branch against main (`main...head`)** — that is the
  cumulative PR, not the increment; every gate round would re-review every
  prior increment.
- **A third mergeability probe at the CI hold** — a third mechanism at one
  boundary (settle probe, hold, merge). The fact rides the read the hold
  already makes.
- **Raise the CI heal cap or lengthen the window** — a counter where a
  measurement belongs; the wait would still end in a ping.
- **Let `accept_close` consume ALL unread steering** — a human's later word
  and a red-CI fact must still run first; spec 041's last-word rule stands.
- **Pin the structural axis per contract revision under `strict`** (the
  evaluator's concern list changed on each of the three 2026-09-09 rounds:
  rows 741–746, 747–752, 753–760, functional count flat at 10/10) — a real
  class, already RULED on 2026-09-09 in spec 044's clarifications
  ("advisory in both modes once every pinned clause is met") and owned by
  spec 044 US2; kept out of this spec at clarify (Denys, 2026-09-10).

## Clarifications

### Session 2026-09-10 (with Denys)

- Q: Should this spec also pin the structural findings under `strict`, so a
  later round cannot add new concerns after the worker fixed the last set?
  → A: **Keep it out.** Ship the three stories; the structural axis is spec
  044's ruling (advisory once the pinned clauses are met) and lands with it.

## Assumptions

- The PR base for goal-branch delivery is the remote default branch; the
  v1-helper `base_branch` seam names any other. Both already resolve in
  delivery (`_default_base_ref`); the span reads the same fact.
- GitHub creates no `pull_request` runs for a PR without a merge ref
  (a `CONFLICTING` PR). Evidence: fs-431 head `5138bf1` carries CodeQL runs
  only, while every earlier head ran Backend CI, Frontend CI and Docker
  Build. This is GitHub's documented behavior for the `pull_request` event.
- No invariant changes: the span keeps one definition (constitution IX
  names it the verdict of record), fail-closed gates stay fail-closed, the
  merge stays at the one seam spec 025 opened.
