# Contract — Label-routed ceremony tiers (US3, FR-004/FR-009)

**Where**: a new pure-function router (proposed `devclaw/goal/tier_routing.py`,
layer 2) called from the two dispatch write-sites — `server/tools.py::dispatch_task`
(companion path, `kind` signal) and the goal advance path in `goal/tick.py` /
`goal/tick_dispatch.py` (issue-label signal, fetched by the existing mechanical
`gh` call at dispatch). Plus the vendored pack inside the packed speckit scaffold
(`devclaw/speckit_setup.py` content + worker skills).

## Function shape

- `route_tier(kind: str | None, labels: list[str] | None) -> Tier` — pure dict
  lookup, no I/O, no LLM. `Tier` carries `name` (`full|bugfix|hotfix|direct`) and
  the brief clause to stamp.
- The advance/dispatch brief builder takes the resolved `Tier` and emits the
  tier-specific instruction block (full-cycle text as today; bugfix → "run the
  vendored bugfix workflow: bug report, regression test BEFORE the fix, minimal
  plan/tasks"; hotfix → expedited variant; direct → "smallest direct fix, create
  NO specs/ artifacts").

## Behavior

| Input | Tier |
|---|---|
| `kind=fix_bug` | bugfix |
| `kind=implement_feature`, no labels | full |
| labels contain `critical-fix` or `hotfix` | hotfix |
| labels contain `feature` or `enhancement` (and no hotfix label) | full |
| labels contain `bug` only | bugfix |
| labels contain `chore` or `docs` only | direct |
| no signal / unknown labels / conflicting (e.g. `feature`+`bug`) | full (careful path) |

**Monotone invariant**: ambiguity NEVER resolves to a lighter tier than any
plausible reading — routing only goes up the ladder or to needs-human. A
`review_repository` kind is out of scope (read-only, no ceremony).

## Vendored pack (FR-009, D9/D11/D12)

- Pinned copy of MartyBonacci/spec-kit-extensions `bugfix` + `hotfix` only, in
  the packed harness; upstream SHA + the single `SPECKIT_NO_BRANCH=1` delta
  recorded in a vendor README. Registered via speckit's own workflow mechanism
  (`workflow add` local path / registry entry) — **no devclaw workflow
  abstraction**.
- The worker consumes the tier's command content as plain markdown + bash
  (Principle II); branch creation inside pack scripts is disabled — delivery
  owns branches (#486 goal-branch contract).

## Invariants

- Routing is zero-token and dispatch-time only (never idle-path; Principle III).
- The worker executes the stamped tier; it does not re-decide (a worker
  "rationalizing down" is the #358 integrity class — enforced host-side).
- Slice-guard, done-gate, verify gate: **unchanged**. `specs/bugfix-*/tasks.md`
  rides the existing glob; the done-gate grounds on the tier's artifact set
  (bug report for bugfix) or, for `direct`, the existing done_when text path.

## Tests (named regressions, SC-005)

- `test_tier_routing.py` — table-driven: every row above; the monotone invariant
  (property: adding an unknown label never lightens the tier); conflicting
  labels → full.
- `test_advance_brief_tiers.py` — brief stamps the right block per tier; the
  `direct` brief forbids artifact creation (assert presence AND absence per
  cognition-prompts rule); idle path still adds 0 cognition calls.
- Vendor integrity: a test asserting the vendored scripts contain the
  `SPECKIT_NO_BRANCH` guard (the one allowed delta) so an unreviewed re-vendor
  can't silently reintroduce branch creation.
