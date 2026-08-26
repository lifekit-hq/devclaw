# Quickstart: validating spec 020

## Stubbed suite (per-increment, CI-equivalent)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q   # full suite; per-story slices below
.venv/bin/ruff check . && .venv/bin/mypy
```

- **US1**: `pytest tests/test_task_retry.py -k oom` — marker class fails fast,
  reason names cap + remedies, runner called once; `tests/test_goal_tick.py
  -k envcap` — adapted brief on first recurrence (contains the cap, NOT the
  "strictly smaller slice" advice), block with `mechanical:env_cap` on the
  second, counter reset on productive settle; `tests/test_eval_outcomes.py`
  — `sandbox_oom` failure class derived.
- **US2**: runner unit tests — `_run_verify`/hooks/mise spawn with the
  score-raising preexec; agent env carries `BASH_ENV`; happy-path outputs
  byte-identical.
- **US3**: `tests/test_sandbox_isolation.py` — argv contains the
  `-e DEVCLAW_SANDBOX_MEMORY/-e DEVCLAW_SANDBOX_CPUS` pair equal to the
  `--memory`/`--cpus` values (same-source assertion); skill file contains the
  bounding guidance (presence AND absence pattern per the prompt-test rule).
- **US4**: `tests/test_sandbox_image_override.py` sibling for sizing
  (registry → queue → EngineRequest → argv); admission test with a per-task
  override consuming the budget; write-time rejection cases (grammar +
  unadmittable); doctor seeded-fault test.

## Live shakedown (the only real-OOM proof; pytest is docker-free)

Prereqs: logged-in `claude`, docker, a deployed instance or `DEVCLAW_ENGINE`
unset locally. Follow `docs/runbooks/live-shakedown.md` conventions.

1. **Shield (US2/SC-002)**: dispatch a task to a throwaway repo whose verify
   runs a memory hog (`python -c "x=bytearray(6*2**30)"`-style) inside a 2g
   sandbox. Expect: the hog dies "Killed", the agent narrates the failure and
   the task settles on its own terms — the session never dies with "exited
   unexpectedly".
2. **Legibility (US1/SC-001)**: same repo, but make the AGENT balloon
   (verify off, prompt directs a huge in-process allocation via the agent
   runtime — or temporarily disable the shield). Expect: ONE dispatch, task
   error `sandbox OOM-killed (cap=2g...)`, goal's next brief carries the
   adapted bounding directive; a second OOM blocks with `mechanical:env_cap`.
3. **Sizing (US4/SC-004)**: `update_project(sandbox_memory="4g")` on the
   throwaway project → next dispatch's container shows
   `docker inspect --format '{{.HostConfig.Memory}}'` = 4294967296 and
   in-sandbox `DEVCLAW_SANDBOX_MEMORY=4g`; a second project stays at the
   default. `update_project(sandbox_memory="64g")` on the 16GB box → loud
   ToolError naming MemTotal.
4. **Doctor**: `doctor` reports the sizing check green; shrink-host scenario
   is covered by the seeded-fault pytest, not live.
