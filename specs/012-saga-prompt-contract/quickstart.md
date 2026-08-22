# Quickstart: validating 012 US1 (increment feed-forward)

## Prerequisites

```bash
pip install -e ".[dev]"
```

The whole slice is validated in the stubbed suite — no docker, no claude
(anything needing the real pipeline is `/live-shakedown`, not pytest).

## Run the named regressions

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_prior_increments.py tests/test_goal_tick.py
```

Expected: the new named tests pass —

- `test_second_increment_brief_states_prior_delivery_outcome_and_verdict`
  (US1 acceptance 1: dispatch after a settled delivery → the brief carries the
  prior increment's objective, status, gate verdict, and PR)
- `test_first_increment_brief_states_no_prior_increments_explicitly`
  (US1 acceptance 2 / FR-004)
- `test_failed_prior_increment_reported_in_next_brief`
  (US1 acceptance 3 / FR-005)
- `test_prior_increments_section_is_bounded_and_elides_loudly` (FR-009b)
- `test_unreadable_delivery_block_degrades_to_stated_gap` (edge case)
- `test_display_goal_annotates_prior_increments` (#550 display half)
- `test_idle_tick_performs_no_delivery_read` (SC-007 / constitution III)

## Full suite

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

Expected: green at or above the baseline in the PR description; every
pre-existing zero-token guard test (`FakeClaude.calls == 0`) untouched and
green — if one fails, the change is wrong, never the test.

## End-to-end shape (stub engine)

The scenario the suite encodes (see `tests/goal_fakes.py` fixtures): seed a
goal → settle one advance via the stub engine → let the next tick dispatch →
inspect the dispatched action's `goal` text for the prior-increments section
(contract: `contracts/prior-increments-section.md`), and inspect the goal log /
notification labels to confirm only the display form (with the
`+prior increments` annotation) reached human surfaces.
