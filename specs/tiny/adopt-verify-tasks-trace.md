# TinySpec: Adopt verify-tasks + trace extensions

**Branch**: feat/adopt-verify-tasks-trace
**Date**: 2026-08-24
**Status**: done
**Complexity**: small

## What

Adopt two vetted spec-kit extensions serving the live-validation arc:
`verify-tasks` (phantom-completion detector — the #359 coverage-theater class
as a tool; optional after-implement hook) and `trace` (requirement → test
traceability matrix — the hand-tool of spec 015's uncovered-scenario check).
Both community-published and unverified, so the tinyspec vetting bar applies:
pinned release, every file read, no catalog `install_allowed`.

## Context

| File | Role |
|------|------|
| `.specify/extensions/verify-tasks/` | New — vendored v1.0.0, pruned to runtime surface |
| `.specify/extensions/trace/` | New — vendored v1.0.0, manifest schema-fixed (see below) |
| `.specify/extensions.yml` | Modified by installer — optional `after_implement` hook |
| `.claude/skills/speckit-verify-tasks*/, speckit-trace-*/` | Generated adapters (installer-owned, never hand-edited) |
| `.claude/rules/speckit-workflow.md` | Modified — vendored-extensions paragraph now covers all three |

## Requirements

1. `specify extension list` shows tinyspec, verify-tasks and trace all Enabled.
2. Installed command content is byte-identical to the vetted upstream release
   (verified by diff at install time).
3. The verify-tasks hook stays `optional: true` — it prompts, never auto-runs.
4. Vendored dirs carry only the runtime surface (manifest, commands, license
   docs) — no upstream test fixtures, dev `.specify/`, or `.github/` exhaust
   (856K / 116 files pruned to 5 for verify-tasks; fixtures would even have
   been linted by ruff/mypy).
5. Suite, ruff, mypy green; harness-docs-map guard green.

## Judgment calls

- trace v1.0.0's manifest is written against an older schema
  (`requires.spec_kit`, bare-string `provides.commands`) and fails 0.16.3
  validation. Patched ONLY the manifest to current schema (two mechanical
  transforms; command files untouched — byte-verified) and installed via
  `--dev` from the vetted local copy. Upstream bug, worth reporting.
- Pruning the vendored dirs deviates from raw installer output on purpose:
  upstream `.specifyignore`s are incomplete, and committing fake fixture
  src-trees into devclaw is noise plus linter surface.

## Tasks

- [x] Download pinned releases; read every file (no scripts/network in either's runtime surface)
- [x] Install verify-tasks via `--from` pinned URL
- [x] Fix trace manifest schema; install via `--dev` from vetted copy
- [x] Prune vendored dirs to runtime surface
- [x] Amend rule paragraph to cover all vendored extensions
- [x] Suite + ruff + mypy green

## Done When

- [x] All tasks checked off
- [x] `specify extension list` shows all three Enabled
- [x] Suite passes; no lint errors
