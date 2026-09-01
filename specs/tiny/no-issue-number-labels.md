# No issue-number labels

## What

`delivery._scope_label` derives a PR label from the conventional-commit
scope. Workers title issue-driven increments `fix(<issue#>): …`, so the
issue number became a repo label (`gh label create --force` mints it) —
finance-sentry grew a label literally named `479`. Ruled by Denys
2026-09-01: a label must describe an area or kind; a bare number describes
nothing and issue↔PR linkage already exists natively.

## Requirements

- An issue-number-shaped scope (digits, optionally `#`-prefixed) is treated
  as no scope: the label falls back to the type (`fix(479):` → `fix`),
  matching the existing scopeless behavior.
- Real area scopes (`fix(queue):` → `queue`) unchanged; labeling stays
  best-effort/non-fatal.

## Plan / Tasks

- [x] `_scope_label`: numeric/`#`-numeric scope ⇒ fall back to type.
- [x] Extend the existing `test_scope_label_from_cc_scope_then_type` cases
  (never mint an instance-test — the class test grows the numeric case).
- [x] One-time cleanup done live: the `479` label deleted from
  lifekit-hq/finance-sentry.

## Done-When

- `fix(479):` and `chore(#123):` label as `fix`/`chore`; suite, ruff, mypy
  green.
