# Quickstart: validating structured problem resolution

Runnable scenarios that prove the feature end-to-end. The stubbed suite
proves the invariants; the live instance proves the flow. Contracts and the
data model are referenced, not repeated.

## Prerequisites

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q      # baseline green before you start
ruff check . && mypy
```

## Stubbed suite — the tripwires this feature must keep green

Named after invariants, not functions; each extends an existing class test or
adds a seeded-fault pair. No test for ordinary rendering or wording.

| tripwire class | test | proves |
|---|---|---|
| zero-token idle | `test_blocked_goal_with_open_problem_costs_zero_cognition` (extends the idle-guard family in `tests/test_goal_tick.py`) | a blocked goal with an open Problem ticks with `FakeClaude.calls == 0`; the timebox check is a timestamp compare |
| zero-token idle | `test_timebox_default_applies_without_cognition` | at `timebox_at`, the default becomes a Decision (`provenance=defaulted`) and the goal UNBLOCKs, still `FakeClaude.calls == 0` |
| CAS / single writer | `test_resolve_problem_is_one_transaction_with_unblock` (in `tests/test_goal_transactional.py`) | a `TransitionConflict` rolls back the Decision row and the Problem status together |
| CAS / single writer | `test_unblock_by_resolution_rides_legal_unchanged` (`tests/test_goal_transitions.py`) | no new `State`/`Event`; the structural guard on `LEGAL` and the single-ACHIEVE-emitter guard stay green |
| fail-closed / done is a proposal | `test_defaulted_accept_and_close_never_emits_achieve` | a defaulted `accept_close` returns the goal to idle; `done` is reached only through the done-gate's existing path; under `strict` the goal stays blocked and no Decision is written |
| brake machinery | `test_steer_goal_is_refused_while_a_problem_is_open` | `steer_goal` raises with the Problem and the two verbs; nothing is written |
| brake machinery | `test_worker_honest_block_raises_a_problem_without_burning_the_cap` (extends `tests/test_task_retry.py` / tick) | one settle → blocked with a Problem; `actions_dispatched` unchanged |
| doctor seeded-fault | `test_problems_tables_absent_detected` / `_present_is_ok`, `test_problem_pointer_drift_detected` (`tests/test_doctor.py`) | drop a table / point `problem_id` at a non-open row → FAIL naming it + "restart"; healthy → OK |
| structural guard | `test_decisions_marker_present_and_capped` (beside the prior-increments guard) | the head line is the marker; the entry list caps under budget; superseded Decisions are absent |
| structural guard | `test_admission_lint_catches_the_three_classes` | each class caught with a named reason; the corrected forms admit; the four 2026-09-02 contracts replay as SC-004 |

Run them: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q -k "problem or decision or admission or timebox"`.

## Live validation — one goal, the whole flow

Against the deployed instance (read-only SSH tunnel `ssh -L 18791:127.0.0.1:18791 lifekit-vps`; devclaw MCP wired to the waiter). Expected outcomes in **bold**.

1. **Admission refusal (US3)** — create a goal whose `done_when` says
   *"a real Telegram brief is sent and sanity-checked"*.
   **Refused, nothing persisted**, the response names the clause and
   `Telegram`. Resubmit with *"the brief job composes the message and the
   composer's output matches the browser Ledger figures in a unit test"*.
   **Admitted.**
2. **Admission rewrite (US3)** — create a goal with the clause *"all tests
   pass"*. **Admitted**; response `admission.rewrites` shows the clause as
   *"no new failures relative to the default branch"*, and
   `get_goal(...).decisions` carries one Decision with `provenance=admission`.
3. **A typed Problem (US1)** — drive a goal to a `needs_human` verdict (a
   contract with an undecided choice will do). `get_goal` shows
   **`problem` with all six fields**, `blocked_on` a one-line summary, and
   the owner ping names the clause, the options with the default marked, and
   the two verbs — **never `steer_goal`**.
4. **Refused steer (US2, Q1)** — `steer_goal` that goal. **Error carrying the
   Problem and the two verbs; nothing written; goal still blocked.**
5. **Decide (US2)** — `decide(goal_id, problem_id, option=<key>)`.
   **Goal idle, cap and churn budgets reset, `decisions` has one row with
   `provenance=owner`, Problem `resolved`.** The next dispatch's brief
   (visible in the task's goal text) carries the **Decisions section** with
   the marker line and the one entry (US4).
6. **The gate honours it (US4)** — let the done-gate run. **The clause is
   graded `resolved_by_decision`, citing the Decision; it is not
   re-litigated.**
7. **Timebox default (US2)** — raise a Problem and do nothing for the timebox
   (or shorten it on a test goal). **At the first tick after expiry: a
   `defaulted` Decision, the goal idle, exactly one ℹ️ ping.** With the
   default `accept_close` under `trust`, the **next done-gate round closes
   through merge-on-close**; set the goal `strict` first and instead observe
   **it stays blocked with a "only an explicit decide can close it" ping.**
8. **Doctor** — `docker exec devclaw-devclaw-mcp-1 python3 -m devclaw doctor`
   shows **✓ `instance.problems.tables`** and **✓ `instance.problems.status_pointer`**.

## The metric (SC-001 / SC-002)

`evals/burn_profile.py`'s sibling query over `goal_problems` /
`goal_decisions`: pings per goal-week and the `provenance` split. Baseline
2026-09-02: 8 pings/day across 10 goals, ~1 needing judgement. Target after
the whole spec lands: at most half the pings; judgement share above half;
zero Problems resolved via prose.
