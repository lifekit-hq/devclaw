# Tasks: One credential registry

## US1 — register once, every hop iterates (P1)

- [X] T001 `devclaw/credentials.py`: `Credential`, `REGISTRY`, `REFUSED`, `GH_TOKEN_PREFIXES`, `required_vars` / `sandbox_vars` / `agent_vars` / `sandbox_env` / `strip_refused` / `well_formed`
- [X] T002 `engine/sandcastle.py`: `_credential_env()` replaces the two per-token forwards; `_strip_api_keys` delegates; payload `agent_env`
- [X] T003 `engine/host.py`: strip via registry; payload `agent_env`
- [X] T004 `boot_guard.py`: `REQUIRED_PRODUCTION_ENV = required_vars()`
- [X] T005 `env_cap.py`, `cognition.py`, `llm_call.py`: prefixes and strip via the registry
- [X] T006 `runner/runner.py`: `_agent_env_vars(req)`; the allowlist forwards the payload names; pre-042 fallback
- [X] T007 `tests/test_credentials_single_registry.py` (structural) + class tests in `tests/test_sandbox_isolation.py`
- [X] T008 `pyproject.toml` import-linter leaf contract
- [X] T009 docs: env-vars rows, architecture bullet, task-execution line, CLAUDE.md OAuth bullet, INDEX currency

## US2 — the hop is verified where the worker runs (P2, shipped 2026-09-10)

- [X] T010 runner: emit the `AgentEnv` event (names present/absent) at session start
- [X] T011 host: record on the task (`queue/settle.py`) and project to one meta row (`env_cap.record_agent_env`); classify a worker env report naming a present credential as present-but-unusable in the row's evidence + remedy, which the hold, the machine issue and doctor all read
- [X] T012 doctor: `instance.registry.token` carries what the last worker session saw, on the same line as the host probe
