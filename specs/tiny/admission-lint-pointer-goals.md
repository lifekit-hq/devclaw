# TinySpec: the admission lint sees a pointer goal's contract, and knows "another repository"

**Issue**: lifekit-hq/devclaw#847
**Branch**: fix/admission-lint-pointer-goals
**Date**: 2026-09-06
**Status**: done
**Complexity**: small

## What

Goal `issue-819-nested-npmrc-2026-09-05` did all its real work, then parked on
a `needs_answer` Problem because its contract carried
"Companion (finance-sentry repo, separate PR): `devclaw.json` declares …" — a
clause no devclaw-checkout sandbox can ever satisfy. Spec 031's admission lint
exists to refuse exactly that at creation. It never saw the clause, for two
reasons, both fixed here:

1. **Pointer goals were not linted.** `create_goal_async` ran
   `lint_mechanical` only on an explicit `done_when`; a goal created from
   `issues=[…]` has none, and its live-fetched contract reached the done-gate
   un-linted. Now the doorway lints the same text the gate will judge
   (`issue_ref.acceptance_contract` over the snapshots it already fetched
   for readiness), refusing creation with nothing persisted.
2. **No cross-repository capability.** `_IMPOSSIBLE` knew credentials,
   messaging, humans, production access. A clause that places its change in
   another repository is now class (a) too, as "a change in another
   repository": an adjective marking the repo as not-this-one (separate /
   another / companion … repo|PR), a repo named outright that is not the
   goal's own (`finance-sentry repo`), or a slug under the goal's own owner
   naming a different repo (`o/other-repo`). The last two need the goal's
   `owner/name`, threaded through as `own_repo`, so a repository merely
   mentioned as context by the goal's own name never refuses.

## Context

| File | Change |
|------|--------|
| `devclaw/goal/admission_lint.py` | `_cross_repo` + `own_repo` on `lint_mechanical` / `lint` |
| `devclaw/goal/issue_ref.py` | `acceptance_contract(snaps)` — the one formatting, shared with `scenarios_contract` |
| `devclaw/goal/service.py` | doorway lints the referenced contract (class (a) only); passes `own_repo` |
| `tests/test_done_when_scenarios.py` | the fail-closed admission case, parametrized over the shapes above |
| `CLAUDE.md`, `docs/architecture.md` | the invariant text says so |

## Rejected alternatives

- **Rewrites (b) and the undecided judge (c) on the referenced contract.**
  The contract is the ticket's and is read live at the gate, so a rewrite has
  nowhere to persist; the readiness grader already judged the ticket's
  choices. Only refusals run on that path — stated at the call site.
- **Refusing any `<name> repo` phrase.** "the git repo", "a monorepo",
  "GitHub repository" are not repositories; a stop-list plus the goal's own
  name keeps context mentions admitted. A false refusal is loud and costs one
  resubmit; a miss falls to the done-gate Problem path, as before.
- **A filing rule instead of code.** Considered (constitution IX: instruction
  before brake) and kept as well — acceptance is this repository's behaviour,
  companion work is its own issue. The brake stays because the lint already
  exists and simply did not run on the path every goal now takes; that is a
  gap in an existing brake, not a new one.

## Done When

- [x] The 2026-09-06 clause, via the issue, refuses creation with nothing persisted
- [x] The same shape as an explicit `done_when` refuses the same way
- [x] A repository named as context, or the goal's own, is admitted
- [x] Suite green, `ruff check .`, `mypy`, `lint-imports` clean
