# Proposal — preserve failed work: a gate-failed task leaves a draft PR, not a vanished diff

- **Status:** **DRAFT** — 2026-08-13. Captured from the night-2026-08-12
  post-mortem; all `[OPEN]`s below are mandatory before LOCK. **No code before
  lock** (delivery-contract change, spec-lifecycle).
- **Date opened:** 2026-08-13 · **Authors:** Denys + Claude
- **Relates to / does not restate:** fail-closed invariants #183/#186 in
  `CLAUDE.md` (this proposal must not weaken them — see Invariants below);
  ADR 0007 (trust dial — the *other* "ship despite a finding" mechanism, which
  only covers dial-able gates and still produces a green PR);
  `proposals/delivery-last-mile.md` (successful deliveries only).

## The incident that surfaced the class

Night 2026-08-12, task `c260caa5` (finance-sentry): the worker built a real
fix (23/23 tests passing) and the verify gate killed it on a structural
false-negative (`dotnet test --filter` exits 1 when one test project has no
matching tests). The task settled `failed`; the diff sat unprotected in the
workspace and would have been wiped by the next dispatch's
`prepare_workspace` hard-reset. It was rescued by hand on 2026-08-13. Good
work should never depend on a human noticing in time.

## Ground truth (code-read 2026-08-13)

- The workspace is a **host bind-mount**; the diff survives the sandbox
  teardown. What destroys it is the **next** dispatch's
  `prepare_workspace` fetch + hard-reset + clean (`engine/workspace.py:208+`).
- Delivery is gated on success: every terminal-failure branch in
  `_run_and_settle` marks failed and returns `None`; `deliver_change` runs
  only under `if deliver and success is not None:` (`task_queue.py:1271`).
  Scoped that way by design history, not by invariant.
- The machinery is ~90% present: `_pr_body` already renders a
  "gate did **not** pass" case that is currently unreachable
  (`delivery/__init__.py:266-271`); `gh pr create` needs only `--draft`; the
  failure reason + last 1500 chars of gate output live on the task row; the
  full per-task event stream survives in `events`.
- Precedent for preserving WIP exists: the usage-limit pause path commits a
  `wip(devclaw)` snapshot (`task_queue.py:1825`) — only for quota pauses.

## Direction

Opt-in, per-project, failure-preserving delivery:

1. **New per-project override `preserve_failed`** (default **off**), same
   pattern as `automerge`/`review_gate`, resolved through the existing
   `resolve_override` seam.
2. On a qualifying terminal failure, devclaw commits the workspace as-is,
   pushes a clearly-named branch, and opens a **DRAFT PR** whose body leads
   with the failure reason and gate output (and links the task trace). A
   comment carries what a reviewer needs to decide: salvage, fix by hand, or
   close.
3. **The task stays `failed`.** The draft PR is a side-artifact for the
   human, not a settle outcome.

## Invariants — respected, not amended

- **#186 verification-fails-closed:** untouched. Nothing unverified ships;
  a draft PR cannot merge itself and the task never reads as approved.
- **#183 no done-without-a-PR:** inverted case (a PR without done) — allowed,
  but the preserved PR must never enter the delivery-success surfaces:
  no `goal_deliveries` row, no `pr_url` on the success path, invisible to the
  goal poller's shipped-work reasoning. Automerge keys off task `done` and
  must never touch it.
- **Loud failure:** the draft PR *is* the loud artifact — the failure gets a
  reviewable home instead of an error string and a doomed diff.

## Slices

- **P1 — verify-gate failures** (~2 PRs): the flag + failure-branch capture +
  draft-PR delivery for the exact class that burned us (real work, gate said
  no). Named regression tests: failed task + flag on → draft PR exists, task
  still `failed`, no `goal_deliveries` row; flag off → byte-identical to
  today.
- **P2 — crash-shaped failures**: review-gate crash (unreviewable diff) and
  wall-clock timeout, where the diff is coherent but unreviewed. Explicitly
  labeled "NEVER REVIEWED" in the PR body.
- **P3 — surface**: the cycle report / console lists preserved-failure PRs so
  the morning sweep sees them without a GitHub crawl.

## [OPEN] — mandatory before LOCK

- **[OPEN-1] Qualifying failure classes.** Verify-gate fail and review-crash
  seem clear; timeout likely; a worker **honest-block** (`needs_answer`) has
  no finished work to preserve — exclude? Where exactly is the line?
- **[OPEN-2] Branch naming + hygiene.** `failed/<task-id>-<slug>`? Auto-close
  the draft PR + delete the branch after N days untouched, or leave cleanup
  to the human? (Anti-accumulation matters — a pile of stale failure-PRs is
  its own dread.)
- **[OPEN-3] Capture point.** Inline in the failure branch of
  `_run_and_settle`, or generalize `_wip_snapshot`? The capture must be
  race-free against a concurrent re-dispatch prepping the same workspace.
- **[OPEN-4] Goal-layer visibility.** Fully invisible to the goal layer
  (safest, P1), or a distinct `preserved_failure` delivery kind the planner
  can *mention* but never count as progress?
- **[OPEN-5] Default scope.** Per-project opt-in only, or also a per-goal
  override (a compounding-experiment goal may want it off to keep the repo
  history clean)?
