# Quickstart: validating spec 021 (worker context-budget)

Prereqs: repo checkout, `pip install -e ".[dev]"`. Everything below is
stubbed — no docker, no `claude`. Live proofs ride `/live-shakedown` at
deploy time (last section).

## 1. Runner-level: tripwire + slice watcher (fake agent)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_runner_acp.py -k "tripwire or slice or cancelled"
```

Expected:
- `usage_window` script: rising `usage_update` stream crosses the threshold ⇒
  exactly one `ContextTripwire` event line, a `session/cancel` + land-now
  `session/prompt` on the fake agent's transcript, result carries
  `tripwire.landed == true` and settles via the normal verify path.
- `slice_flip` script: fake agent flips a full `[US1]` slice then touches a
  `[US2]` row ⇒ watcher stops the turn; result `chunk.stopped_by_watcher ==
  true`, `advanced_slices == ["US1"]`.
- No usage stream + threshold configured ⇒ result carries
  `usage_absent_note`, behavior otherwise byte-identical (FR-007).
- Plain `cancelled` stopReason without land-now ⇒ `status == "error"`, never
  `ok` (the closed hole).
- Single-slice / no-specs workspace ⇒ watcher disarmed, zero new fields
  beyond `context` (FR-005).

## 2. Host-level: continuation brief + oversized-slice re-dispatch

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_goal_tick.py tests/test_thin_plan_advance.py -k "chunk or oversize or continuation"
```

Expected (asserted on `FakeEngine.dispatched` briefs):
- Continuation brief size is bounded (prior-increments cap holds) regardless
  of how many increments precede it (FR-003).
- After a settle carrying the oversized-slice mark, the next brief demands a
  tasks.md re-slice of that slice and an identical re-dispatch is refused
  (FR-008).
- Corrupt/absent tasks.md mid-arc ⇒ loud block, `FakeClaude.calls == 0` on
  the blocked path (FR-004 + zero-token guard).

## 3. Settle-level: problem row + failure classification

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_task_retry.py tests/test_goal_store*.py -k "tripwire or problem"
```

Expected: a result with `tripwire` ⇒ one `record_problem` row
(`limit|context_tripwire`, recovered per `landed`); `list_problems` shows it;
cycle-report machinery counts it (SC-005 readable).

## 4. Full gate before PR

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q   # full suite ≥ baseline
ruff check . && mypy                                 # both clean
```

Skill-text changes: `tests/test_runner_skills.py` ceilings bumped
deliberately in the same PR if the brief grew; prompt-content tests assert
presence AND absence per the repo convention.

## 5. Live proof (deploy-time, not pytest)

Via `/live-shakedown` after the sandbox image rebuild:
- Arm a deliberately multi-slice ask on a real repo; observe one PR per
  slice-session, `ContextTripwire` absent on healthy slices.
- SC-001/SC-005 measurement: `list_problems` — the
  `context_overflow`/`prompt-too-long` fingerprint stops accruing across run
  nights; `context_tripwire` firings trend toward zero as sizing beds in.
