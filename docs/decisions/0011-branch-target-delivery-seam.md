# ADR 0011 — v1-helper P1: the branch-target delivery seam

- **Status:** accepted 2026-07-27 (Denys — "do it", greenlighting the LOCKED
  P1 slice). Graduated from `proposals/v1-helper-resurface.md` §3/§5; that
  proposal stays the home of the still-deferred P2 (console "file a task"
  surface) and P3 (direct-path prep ergonomics). Freezes the P1 decision.
- **Scope:** delivery + dispatch plumbing only (layers 1 and 4). No goal-layer,
  gate, or cognition change.
- **Shipped as:** PR #387 (delivery seam) + PR #389 (dispatch wiring +
  hardening).

## Context

devclaw's first useful form was a **direct task-runner**: hand it a bounded unit
of work (`dispatch_task`), it runs it sandboxed, gates it, and opens a PR — no
durable goal, no heartbeat. That path never rotted (it is the same
queue → engine → gates → `deliver_change` machinery the goal layer drives per
action), but it lost its surface and one structural capability: **every delivery
branched a fresh derived name off `main` and PR'd to `main`.** It could not
continue an existing feature branch or target a non-`main` base — the wall named
in the vault as `speckit-handoff-gap` (devclaw could never see a
`specs/NNN/spec.md` living on a feature branch), and the reason the v1 helper
stopped being a daily driver.

The investigation grounding the proposal found the fix pre-planned: base=`main`
lived in exactly two spots (`_default_base_ref` and the `--base`-less
`gh pr create`), branch-reuse machinery already existed gated to
`current.startswith("goal/")`, and `prepare_workspace(branch=...)` already
checks out any branch.

## Decision

`deliver_change` gains two optional, blank-safe delivery inputs, threaded from
`dispatch_task` through the queue:

- **`base_branch`** (default `None` → today's `origin/HEAD`→`main`→`master`
  chain) — the PR base. Grounds the ahead-count/diff range
  (`_default_base_ref`) and becomes `gh pr create --base <base_branch>`.
- **`target_branch`** (default `None` → today's fresh derived `feat/…`/`fix/…`
  name) — pins the delivery branch. The queue preps the workspace ON it
  (`prepare_workspace(branch=target_branch)`, created off `base_branch` when it
  doesn't exist yet), and delivery reuses the goal-mode "one branch → one PR"
  path, its predicate widened from `startswith("goal/")` to "on a caller-pinned
  branch". No new reuse logic.

Three hardening rules came out of the tranche's invariant review, all loud-fail:

- **A pinned-target miss is a delivery failure.** If `target_branch` was set
  and delivery did not land on it, the task settles `failed` — the "continue
  this branch" contract never silently degrades into a fresh-branch PR.
- **A bogus `base_branch` fails at dispatch, not downstream.** The queue
  verifies the base resolves in the workspace before the engine runs; an
  unresolvable base fails the task with an actionable message instead of
  letting the diff-range/PR-base skew arise inside delivery.
- **A target equal to the base — or to the remote default branch — is
  rejected before anything runs.** Such a contract would put the workspace ON
  the base itself and delivery's branch-reuse mode would push unreviewed
  commits straight to it, failing only afterwards on `gh pr create` (loud but
  irreversible). devclaw never pushes a base/default branch directly; every
  change ships as a PR.

## Consequences

- devclaw can take **continue-the-branch, PR-to-a-feature-base work** — the
  speckit-shaped handoff it structurally couldn't touch. The direct runner is
  again a commandable daily driver at the delivery level; the *surface* that
  pitches it (console "file a task") is P2, deliberately after this seam.
- Omitted params are byte-identical to prior behavior; the goal layer passes
  neither and is untouched. Existing callers and test stubs needed no changes.
- Delivery still **fails closed** (#183): every new leg (push/PR failure on a
  pinned branch, target miss, bogus base) settles `failed`, never a silent
  success. The gates run unchanged — the seam moves *where the result is
  delivered*, never whether it is gated.

## Invariants (checked, not moved)

OAuth-only, single-writer (TaskQueue owns task rows; no goal-store writes from
delivery), zero-token idle guard (prep is subprocess-only, dispatch-time only),
model-agnostic layer 5 — all untouched. Named regression tests per behavior:
branch-target reuse, base grounding, legacy byte-identity, fail-closed on
caller-chosen branches, pinned-target miss, bogus base.
