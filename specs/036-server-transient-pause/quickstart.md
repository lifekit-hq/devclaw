# Quickstart — validation scenarios

All scenarios run under the stubbed suite (`DEVCLAW_ENGINE=stub`, no docker,
no `claude`). Run with a private tmpdir:

```bash
TMPDIR=$(mktemp -d) python -m pytest -q tests/test_limits.py tests/test_rate_limit_pause.py tests/test_goal_rate_limit.py
```

## Scenario 1 (US1) — the outage pauses the queue instead of failing the task

Given a submitted task whose engine result is
`{"status": "error", "error": "session/prompt failed: Internal error: API Error: 529 Overloaded"}`,
when the queue pumps it, then:

- the task's status is `queued` again (requeued), never `failed`;
- `store.global_pause()` returns a future `paused_until` whose reason starts
  with `server_error`;
- the engine ran exactly ONCE — no second attempt inside the outage.

## Scenario 2 (US1) — one accurate ping, zero tokens, auto-resume

Given the pause set above, when `tick_all` runs twice, then every goal
reports `RATE_LIMITED`, `FakeClaude.calls == 0`, and exactly one owner ping
is sent whose text names a provider outage and NOT a usage limit. When the
pause window elapses and `tick_all` runs again, the pause is cleared and
exactly one resume ping is sent, matching the pause's wording.

## Scenario 3 (US1) — app-domain 5xx prose never pauses the account

`classify_failure("AssertionError: expected 200 got 503")` stays `REAL`;
`classify_failure("503 Service Unavailable")` stays `TRANSIENT` and
`is_pausing is False`. `classify_failure("API Error: 529 Overloaded")` is
`SERVER_ERROR` and `is_pausing is True`. A 529 string that also carries
usage-limit wording is still `QUOTA`.

## Scenario 4 (US2) — the ladder escalates and resets

Three consecutive provider-outage pauses in one episode produce 300, 600 and
1200 seconds; the fifth clamps at 1800. A stated `Retry-After: 45` wins over
the ladder. After a task settles successfully, the next provider outage
pauses for 300 again.

