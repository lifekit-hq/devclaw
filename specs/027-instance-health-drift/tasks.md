# Tasks — spec 027 instance health drift

## [US1] Instance health drift probe

- [x] Create `devclaw/goal/health_drift.py` (probe fns + orchestrator)
- [x] Add 4 config accessors to `devclaw/config.py`
- [x] Add `_maybe_check_health_drift()` edge to `devclaw/goal/service.py`
- [x] Add 4 env-var rows to `docs/reference/env-vars.md`
- [x] Create `tests/test_health_drift.py` (T1/T2/T3)
- [x] Run `pytest`, `ruff check .`, `mypy`; verify all green
