# Tasks — spec 025 dispatch brief budget

## US1 — Brief steering budget + telemetry [P1]

- [x] Add `STEERING_KEEP`, `STEERING_TRUNCATION_MARKER`, `cap_steering()` to `prompt_budget.py`
- [x] Apply `cap_steering()` in `tick._advance_brief` (steering section)
- [x] Log `dispatch brief: N chars` in `tick_dispatch._dispatch_action` after commit
- [x] Add `brief_chars: int` to `DispatchEvent` in `trace.py`, pass it from `_dispatch_action`
- [x] Write `tests/test_advance_brief_budget.py` with three named regression tests:
  - `test_steering_section_bounded_and_newest_line_survives`
  - `test_brief_bounded_with_large_accumulated_history`
  - `test_dispatch_logs_brief_size`
- [x] Run full suite + ruff + mypy — green
