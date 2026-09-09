# Tiny spec — a worker-reported env gap defers to the credential's own probe

## North-star case

- **Failure moved**: *stopped when it shouldn't.* Three goals
  (`fs-557-remove-sandbox-lore`, `scanner-broad-universe`,
  `issue-493-fix-hardcoded-test`) are parked on a `mechanical:env` hold for a
  credential the instance already has — doctor reads
  `instance.registry.token: ok — NODE_AUTH_TOKEN set, well-formed, and
  accepted by GitHub (HTTP 200)` while the hold says the sandbox lacks it.
- **Number**: clean-cycle rate, **3/11** on 2026-09-09, with `mechanical:env`
  wedging **5 of those 11** cycles; devclaw-caused idle **29%** of observed
  time. Secondary: **4 of the 12 `resume_goal` calls** in the 14 days to
  2026-09-09 were a human clearing this one class by hand.
- **Cut condition**: if a superseded hold ever releases while the gap is real —
  a green probe next to a worker report that is still true — the supersede is
  wrong and this is reverted to the human-only exit. One occurrence is enough.

## What

**Amends `specs/tiny/env-hold-observes-the-capability.md` (and through it spec
032 US2).** A worker-reported environment deficiency whose prose names a
credential in the registry (`devclaw/credentials.py`, spec 042) is **superseded
while that credential's own probe reads green**. It stops holding dispatch, and
the existing `mechanical:env` auto-heal lifts the block on its own budget.

Prose that names no registered credential is **unchanged**: it stays red until
a human vouches via `resume_goal`. Declared-capability holds are unchanged.

## Context

The prior tinyspec was right to delete the `env_ref` heal — the ref is
anti-correlated with the fact. But it accepted a permanent hold as the price,
and said so in its own rejected-alternatives section:

> A credential appearing changes no digest, so the hold would then never clear
> mechanically and we would have traded a false heal for a permanent one.

That price came due. `WORKER_PREFIX`'s docstring states the premise that makes
it unavoidable:

> It has no probe runner — a worker invents the id from prose, so nothing can
> ever read it green mechanically.

**Spec 042 falsified that premise five days later.** The credential registry
made a subset of worker prose mechanically checkable: when a worker says "the
sandbox lacks `NODE_AUTH_TOKEN`", devclaw now has that exact credential in ONE
registry, with a live probe (`probe_registry_token`) that doctor already runs
and that the sweep already refreshes into a persisted row. The fix is not a new
signal — it is *reading the signal already sitting next to the stuck row*. On
2026-09-09 the finance-sentry project carried both at once: a green
`registry:npm-github` row and a red `worker:node-auth-token-…` row, and the
loop obeyed the one with no probe.

This is the class, not the instance: **a brake that cannot observe its own
release condition.** `CLAUDE.md` already forbids it — *"a kind with neither a
heal nor a declaration strands the goals it parks"* — and
`tests/test_mechanical_blocks_are_recheckable.py` enforces it at the level of
the *kind*. `mechanical:env` has a heal, so the guard passes; the unreckeckable
thing is one level down, in the capability ROW. The guard is extended to see
that level.

**Rejected alternative — a probe runner for arbitrary worker prose.** Cannot
work and would be the fourth mechanism at this seam: the worker writes free
text, so there is nothing to dispatch on. Constraining the supersede to
credentials the registry already declares is what makes it mechanical rather
than a guess.

**Rejected alternative — clear the row at record time by mapping prose onto the
declared capability.** Loses the worker's report as evidence, needs a migration
for the rows already stuck, and splits the decision across two seams. Reading at
`red_caps_for` fixes the three parked goals with no migration and keeps the
report as provenance.

**Deliberately out of scope:** the report still files its issue and still shows
in the problems catalog. A superseded row is not deleted — if the credential
later breaks, its probe goes red and the report holds again, which is correct.

## Requirements

- **R1** `red_caps_for` does not count a worker-reported row as red while the
  credential its prose names has a persisted **green** probe result.
- **R2** The supersede is registry-driven: the mapping is built from
  `devclaw/credentials.py` objects, so no credential name is spelled a second
  time in the package (the existing build guard stays satisfied).
- **R3** A worker row naming no registered credential, or naming one whose
  probe is red or unknown, holds exactly as today — the human-only exit
  (`resume_goal`) is untouched. `unknown` is not green.
- **R4** Admission and the auto-heal agree by construction: both read
  `red_caps_for`, so the supersede cannot unblock a goal the dispatch guard
  would immediately re-block.
- **R5** The structural guard covers the row level: a superseding mapping whose
  target capability has no probe runner fails the build.

## Plan

1. `env_cap.py` — a registry-driven `_SUPERSEDING_CREDENTIALS` map and a
   `superseding_capability(cap_id)` resolver; `red_caps_for` consults the
   mapped capability's persisted result. Amend the `WORKER_PREFIX` and
   `clear_worker_deficiencies` docstrings, which state the falsified premise.
2. Tests — extend `test_mechanical_blocks_are_recheckable.py` (the class file;
   never a sibling) with the row-level cases: superseded-by-green,
   still-held-on-red/unknown, still-held-for-unmapped-prose, and R5's
   mapping-has-a-runner guard.
3. Docs — `CLAUDE.md`'s mechanical-blocks clause states the amended rule.

## Tasks

- [x] T1 env_cap supersede map + resolver + `red_caps_for` (R1, R2, R3)
- [x] T2 docstring amendments where the old premise is stated (R1)
- [x] T3 tests: extend the recheckable class guard (R1, R3, R5)
- [x] T4 docs honesty — `CLAUDE.md`'s mechanical-blocks clause. **No
  `docs/INDEX.md` currency change:** `docs/runbooks/doctor.md` was read and
  states the *filing* rule, never the human-only exit, so it is not made
  wrong by this change. Nothing edited, nothing to re-date.

## Done when

- A worker-reported gap naming a registry credential stops holding once that
  credential's probe reads green, with no human verb.
- The same gap still holds when the probe is red or unknown, and unmapped prose
  still holds until `resume_goal`.
- The structural guard fails on a mapping with no probe runner.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
