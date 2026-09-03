# TinySpec: doctor flags a tracked `.specify/feature.json`

**Issue**: none (live-instance incident, finance-sentry, 2026-09-02/03)
**Branch**: fix/doctor-tracked-feature-json
**Date**: 2026-09-03
**Status**: implemented
**Complexity**: small

## What

Three finance-sentry goal PRs (#541, #546, #552) were CONFLICTING with main
at once on 2026-09-03, and one of them wedged the 2026-09-02 cycle as
`mechanical:merge_failed`. Every one of them carried `.specify/feature.json`:
finance-sentry tracks that file on main and has no `.specify/.gitignore`,
while devclaw's scaffold gitignores it as per-checkout state (speckit
re-points it every run). Any two goal branches landing in sequence therefore
conflict on it, deterministically. The repo fix is a one-file PR on
finance-sentry; this spec is the deployed-instance sibling (spec 016 FR-014):
doctor sees the shape in ANY registered repo so it cannot recur silently.

## Context

| File | Role |
|------|------|
| `devclaw/doctor/checks_project.py` | Modified — new `project.scaffold.tracked_state` check, registered after `scaffold.drift` |
| `tests/test_doctor.py` | Modified — seeded-fault test |
| `devclaw/speckit_setup.py` | Read only — `_SCAFFOLD_FILES` already carries the `.gitignore` that excludes the pointer |

## Requirements

1. A registered project whose checkout tracks `.specify/feature.json` gets a
   WARN naming the file, the conflict consequence, and the untrack remedy.
2. A checkout where the pointer is untracked (scaffold-gitignored) is OK; a
   project with no `.specify/` is OK (not onboarded); a non-git workspace is
   UNKNOWN. The check is read-only, one `git ls-files`, zero cognition.
3. Seeded-fault test `test_tracked_feature_json_is_flagged_as_merge_conflict_fuel`
   in `tests/test_doctor.py` covers ok → warn → ok.

## Plan

1. Add `check_tracked_checkout_state` next to `check_scaffold_drift` and
   register it in `PROJECT_CHECKS`.
2. Seeded-fault test: scaffold a workspace, `git init`, force-add the pointer
   ⇒ WARN; `git rm --cached` ⇒ OK.

## Tasks

- [x] check + registration
- [x] seeded-fault test
- [x] ruff + mypy + full suite green

## Done-When

`doctor` on the live instance reports `project.scaffold.tracked_state` WARN
for finance-sentry until its untrack PR lands, then OK.
