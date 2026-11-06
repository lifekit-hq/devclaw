# TinySpec: the done-gate checks the PR's files against the files the issue and the owner name

**Branch**: fm/dc-978-diff-scope
**Date**: 2026-11-06
**Status**: delivered
**Complexity**: small (one pure function in `donegate.py`, one tick step, one `gh pr diff` read, one tripwire test, two doc paragraphs)
**Issue**: #978 — the session can widen scope and the PR carries changes nobody reviewed

## North-star case

- **Failure moved**: *ran and produced garbage* — scope creep reaches a PR that
  the done-gate then judges only against the contract's clauses.
- **Number that shows it**: merged goal PRs that touch a file the issue and the
  owner's words never name: unbounded before, 0 after for any issue that names
  a file (an issue naming none states no scope and is not checked).
- **Cut when**: the first-pass rate shows the check blocking goals whose extra
  files were legitimate more often than it catches real creep — then the
  matching rule loosens or the check goes.

## What

Before a head's FIRST review session, `_scope_gate` (`goal/tick.py`) reads the
PR's changed files (`gh pr diff --name-only`, the whole PR against its base —
not `HEAD~1`, which would see one commit of a multi-commit goal) and compares
them with the paths written in the goal's objective, the issues' titles and
bodies, and the owner's instruction comments (`donegate.scope_extras`:
deterministic, no model). The "plan" the issue speaks of has no host-side
source (pillars 3 and 4), so the issue and the owner's words are the plan; the
session's intent file is the issue verbatim and lives in a temp dir.

- Every changed file is named (by path, directory, bare file name, or a test
  sharing a named file's stem), or is under `.devclaw/` → the review runs.
- A file named nowhere → a `scope` record on the PR listing them and ONE
  correction session (the record is in its prompt; it reverts and proposes
  DONE again, or ends BLOCKED with the reason). A second excess → `_block`
  with kind `SCOPE_EXCEEDED`; the goal stops.
- An owner comment newer than the `scope` record waives the check.
- An unreadable file list never passes (`unreadable`, no session).
- Text naming no file at all → `[]`: with no stated scope there is nothing to
  exceed (the check cannot fire on an issue like #978 itself).

## Tasks

- [x] `github.pr_files`, `WorldReader.files`
- [x] `donegate.scope_extras`, `tick._scope_gate`
- [x] `test_the_scope_gate_fails_closed_before_any_review_runs`
- [x] `docs/architecture.md`, `docs/flows/delivery.md`, `docs/INDEX.md`

## Done-When

A PR changing a file named nowhere never reaches a review session; one
correction, then `SCOPE_EXCEEDED` stops the goal; the goal layer stays under
1,500 lines (1,492); `ruff`, `mypy`, `lint-imports` and the suite are green.
