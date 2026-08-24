# Quickstart — validating spec 015 (live-validation loop)

Everything stubbed — no docker, no claude, no network.

## US1 — acceptance tests as ground truth

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_acceptance_executability.py
```

Proves: the intake prompt carries the executable-test element (presence) and
the raw template lacks the marker being asserted (absence discipline); a
FakeClaude not-ready verdict with the new missing-vocabulary lands
`needs-refinement` (SC-005); the done-gate prompt enumerates uncovered
acceptance scenarios on the structural axis; the browser gate's executed-run
requirement is pinned to FR-002 by a named test.

## US2 — the validator

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_validate_product.py tests/test_validate_product_settle.py
```

Proves: the runner's agent-less branch (boot → suites → report, failing-test
title extraction, boot-failure and missing-report degradation, partial
coverage note); the settle path files one schema-conformant 014 finding per
failure with no PR / no commit / no gate verdict; a green run files nothing
and leaves a run record; the workspace is restored after the run.

## US3 — companion-first triggering

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_qa_goal.py
```

Proves: a completed deploy enqueues exactly one validation run for an
opted-in repo (and one read-only prod smoke); no qa goal ⇒ nothing triggers;
schedule OFF ⇒ idle ticks stay zero-cognition (`FakeClaude.calls == 0`);
armed cadence enqueues inside the run window and disarming stops it; the qa
goal never plans, never opens the done-gate, and never holds the project's
single-writer slot.

## Full gate

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy
```
