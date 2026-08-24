# Quickstart — validating spec 014 (issue doorway)

## Prerequisites

```bash
pip install -e ".[dev]"
```

Everything below is stubbed — no docker, no `claude`, no network.

## Validate US1 — schema round-trip + fail-loud

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_issue_doorway.py
```

Expected: green. The named tests prove
- a `MachineFinding` renders to a body that parses back field-identical
  (SC-001),
- the metadata line carries `v1` + fingerprint + source + severity
  (contract regex),
- a mandatory field with no meaningful value renders as the literal `unknown`,
- a failing fake `gh` yields `FilingOutcome(action="failed", reason=…)` plus a
  problems-catalog row — never a silent drop (US1 scenario 3).

## Validate US2 — idempotency by fingerprint

Same module; the dedup tests prove
- second filing of an open fingerprint → `updated`, one issue, occurrence 2
  (SC-002),
- filing after close → `reopened` with the regression-marked comment.

## Validate US3 — catalog migrated, single writer

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_issue_doorway_migration.py tests/test_issue_doorway_single_writer.py \
  tests/test_self_issue.py
```

Expected: green. Proves the catalog path files schema-conformant bodies with
its legacy labels/linkage intact, and the AST guard finds no issue-creation
call site outside `devclaw/issue_doorway.py` + `devclaw/intake.py` (SC-004).

## Validate FR-008 — gradeable without grading changes

Covered by a named test in `tests/test_issue_doorway.py` that runs a rendered
doorway body through the intake grading parse path (stubbed cognition) and
asserts it grades without error (SC-003's stub half).

## Full gate before PR

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy
```
