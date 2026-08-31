# Tasks — spec 027 instance health drift

## [US1] Instance health drift probe

- [x] Create `devclaw/goal/health_drift.py` (probe fns + orchestrator)
- [x] Add 4 config accessors to `devclaw/config.py`
- [x] Add `_maybe_check_health_drift()` edge to `devclaw/goal/service.py`
- [x] Add 4 env-var rows to `docs/reference/env-vars.md`
- [x] Create `tests/test_health_drift.py` (T1/T2/T3)
- [x] Run `pytest`, `ruff check .`, `mypy`; verify all green

## [US1-increment2] Docker root disk probe (auto-eval correction)

- [x] Add `_docker_root_dir()` + `_docker_disk_used_pct()` + `_check_docker_disk()` to `devclaw/goal/health_drift.py`
- [x] Add `health_docker_disk_warn_pct()` accessor (`DEVCLAW_HEALTH_DOCKER_DISK_WARN_PCT`) to `devclaw/config.py`
- [x] Pass `docker_disk_warn_pct` to `run_health_drift_checks()` in `devclaw/goal/service.py`
- [x] Add `DEVCLAW_HEALTH_DOCKER_DISK_WARN_PCT` row to `docs/reference/env-vars.md`
- [x] Extend `tests/test_health_drift.py` with T4 cases and `_docker_disk_used_pct` mocks
- [x] Run `pytest`, `ruff check .`, `mypy`; verify all green
