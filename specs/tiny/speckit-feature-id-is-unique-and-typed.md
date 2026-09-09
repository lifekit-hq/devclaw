# TinySpec: a feature id is unique across goals, and a number never poses as an issue

**Branch**: `fix/speckit-feature-id-is-unique-and-typed`
**Date**: 2026-09-09
**Status**: IMPLEMENTED 2026-09-09 — reviewed and admitted by Denys
**Complexity**: small (one worker skill file)

## North-star case

- **Failure moved**: ran and produced garbage. The plan of record the worker skill relies on for its own handoff is ambiguous, and the commit scope names a number whose namespace nothing declares.
- **Number that shows it**: colliding `specs/NNN-*` prefixes in the repos devclaw drives. finance-sentry today: 3 collided prefixes across 7 directories, plus 1 directory numbered from the issue namespace.
- **Cut when**: two goals branch concurrently off one main and land distinct, meaningful feature ids without a collision. If they still collide, the allocator is the problem and the fix is not an instruction.

## Root cause

Denys spotted this on 2026-09-09: some tasks put a speckit number where an issue number belongs.

`runner/skills/_writes-code/05-speckit-memory.md` tells the worker to create a feature with `.specify/scripts/bash/create-new-feature.sh`, which allocates the next sequential `NNN` by reading the `specs/` directory it can see. Under the one-goal-one-checkout invariant (2026-09-06) each goal runs in `.goals/<goal_id>`, a clone that carries only its own branch. So every goal branching off the same main sees the same `specs/` and allocates the same next number. The checkout serializes files; nothing serializes the identifier.

Live in finance-sentry, on the fs-431 goal branch:

```
042-net-worth-stacked-lines-toggle   043-infra-free-proof     044-action-tickets
042-verify-gate-443                  043-portfolio-scanner    044-counterparty-flows
                                                              044-hygiene-sentinels
421-asset-dossier                                             044-weekly-performance-brief
```

Four features numbered 044. Two 042s, two 043s. And `421-asset-dossier`, where a worker used the GitHub issue number as the feature number, which both breaks the sort and borrows a namespace that means something else.

Two consequences, and the second is the one that bites:

1. **The commit scope lies by omission.** The same skill's commit rule is `type(scope): what changed`, and workers put the number there: `fix(044)` is a spec, `fix(421)` and `feat(554)` are issues. Nothing in the form distinguishes them. That is what Denys saw.
2. **The handoff rule stops resolving.** `05-speckit-memory.md` says to resume "the smallest not-yet-complete `specs/NNN-*/`". With four 044s that is a tie, and `421-` sorts after `046-`. A session can adopt another goal's plan as its own prior self's handoff.

This is the same shape as the 2026-09-06 finding that two goals on one directory handed each other's commits to the gates. Same class, different artifact: **a per-checkout allocator minting what is really a global identifier.**

## Requirements

1. A feature directory name is unique without coordination between checkouts. The issue number is the natural candidate when the goal is issue-backed, because it is already globally unique and already means exactly this feature. A slug-only name is the fallback when there is no issue.
2. Whatever the naming rule is, the handoff rule in the same file resolves to exactly one directory, deterministically, with no tie.
3. The conventional-commit scope is a component or area, never a bare number. `fix(sentinels)` over `fix(044)`. A tracked issue is linked the way the commit skill already says, with `Fixes #<n>`, which is the one place a number carries its namespace.
4. One file changes: `runner/skills/_writes-code/05-speckit-memory.md`, with the scope line in `90-commit.md` if it needs sharpening. Worker-kind instructions have exactly one home (constitution II), so no second copy.

## Open question for Denys

Renaming the seven existing collided directories in finance-sentry is a separate decision. It rewrites paths that landed commits reference, and `044-hygiene-sentinels` is live under fs-431 right now. My recommendation is to leave them and fix forward, because the instruction stops new collisions and a rename touches a branch that is already wedged. Say if you want the cleanup and I will scope it separately.

## What the implementation settled

Requirement 1 offered the issue number as the natural unique candidate. Reading
the tool decided against it, twice over:

- `create-new-feature.sh --number N` documents itself as *"prefer a feature
  number (**auto-corrected if its specs prefix exists**)"*. On the exact
  condition this spec exists to fix — a prefix already taken — it silently
  picks a different number. A collision-proof rule cannot be built on a flag
  that quietly yields on collision.
- The root cause already convicted `421-asset-dossier`: an issue number in the
  feature slot sorts wrong and borrows a namespace that means something else.
  Requirement 3 keeps the issue where its namespace is stated — `Fixes #<n>`.

So the identifier is `--timestamp` (`YYYYMMDD-HHMMSS-<slug>`), a first-class
flag of the standard tool, unique without coordination and correctly sortable.
No devclaw-specific mechanism, per IX.

The handoff rule (requirement 2) is stronger than a tie-break: **the goal's
branch is its identity**, so the feature this session owns is the one this
branch ADDED —
`git diff --name-only --diff-filter=A origin/HEAD...HEAD -- specs/`. It cannot
tie, and it cannot adopt another goal's plan: another goal's feature is either
on the default branch (not added here) or on a branch this checkout does not
have. That reuses the one-goal-one-checkout invariant rather than inventing a
rule beside it.

**The eval task has no lane.** `tests/cognition/` is evaluator-only; there is
no worker-instruction eval harness, and building one is a new mechanism far
outside a one-file tinyspec (and the "standard practice over a devclaw-specific
mechanism" rule). The instruction ships with a structural guard in
`tests/test_runner_skills.py` instead — the shape
`test_the_skill_bundle_licenses_no_gate_bypass` already uses for exactly this
job — and the real measurement stays the north-star number: collided `specs/`
prefixes in the driven repos. A worker-instruction eval lane is worth its own
decision, not a line smuggled in here.

## Rejected alternatives

- **A devclaw-side allocator that hands the worker a number.** That puts project artifact naming inside devclaw, which IX forbids, and invents a mechanism where a naming rule suffices.
- **A gate that fails a task on a duplicate prefix.** A brake where an instruction works, and it would fail tasks for a condition created by a previous goal.
- **Patch `create-new-feature.sh` in each driven repo.** devclaw editing a project's own tooling is a gate-input edit by another name, and the `change_class` gate exists to stop exactly that.

## Tasks

- [x] Naming rule (`--timestamp`, never an issue number) + branch-scoped handoff rule in `05-speckit-memory.md`
- [x] Commit-scope line in `90-commit.md` — scope is a component, never a bare number
- [x] Structural guard in `tests/test_runner_skills.py` in place of an eval fixture — see *What the implementation settled*; the worker-instruction eval lane does not exist and is not this spec's to build
- [x] Stale `specs/NNN-*/` references dropped from `07-agents-md-honesty.md` and `onboard/00-onboard.md` — the retired shape must not survive anywhere in the bundle
- [x] The writes-code brief stays under its 14,500-byte ceiling (14,482) — the new doctrine paid for by compressing redundancy, not by lifting the bar

## Done-When

Two goals branching concurrently off one main produce distinct feature directories. No new commit lands with a bare number as its conventional-commit scope. The handoff rule names exactly one directory on the fs-431 branch.
