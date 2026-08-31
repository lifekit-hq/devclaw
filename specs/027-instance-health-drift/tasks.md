# Tasks — spec 027 instance health drift

## [US1] Instance health drift probe

- [ ] Create `devclaw/goal/health_drift.py` (probe fns + orchestrator)
- [ ] Add 4 config accessors to `devclaw/config.py`
- [ ] Add `_maybe_check_health_drift()` edge to `devclaw/goal/service.py`
- [ ] Add 4 env-var rows to `docs/reference/env-vars.md`
- [ ] Create `tests/test_health_drift.py` (T1/T2/T3)
- [ ] Run `pytest`, `ruff check .`, `mypy`; verify all green
