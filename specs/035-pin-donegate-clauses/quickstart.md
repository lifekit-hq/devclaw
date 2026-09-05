# Quickstart validation — spec 035 pinned done-gate clauses

Everything validates in the stubbed suite (no docker, no claude) plus one
live observation after deploy. Fixtures: `tests/goal_fakes.py` (`FakeClaude`,
`seed_goal`); done-gate behavior lives in the existing done-gate test module
— extend its named cases, do not mint siblings (tripwire rule).

## Prerequisites

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q   # green baseline first
```

## Scenario 1 — one decomposition per revision (US1, SC-001)

Stub `FakeClaude` to return different clause arrays on successive calls
(the fs-479 variance). Drive three done-gate rounds against one unchanged
contract digest. Expect: the pin row exists after round 1; rounds 2–3
receive the pinned list in their prompt (assert the rendered prompt carries
the ids and NOT the decomposition instruction); recorded clause count
identical across rounds; exactly one row in `goal_contract_pins`.

## Scenario 2 — unknown id fails closed, no churn charge (FR-002/FR-006)

Stub a pinned-mode verdict referencing `c99`. Expect: round settles
fail-closed (no close, no accounting change), `donegate_rounds` NOT
incremented, a problems-catalog entry recorded, pin untouched.

## Scenario 3 — monotonic accounting + flip rule (US2, FR-011)

Rounds satisfy `c1`, then `c1+c2`, then flip `c1` without `flip_cause`.
Expect: progress 1 → 2 against a constant denominator; the flip round is
malformed (fail-closed, no churn increment); a second flip WITH a cited
`flip_cause` is accepted and progress drops with the cause in the rationale.

## Scenario 4 — amendment re-pins once with carry-forward (US3, FR-003/FR-007)

Change the stubbed contract digest between rounds, keeping two clauses
byte-identical (one previously satisfied, one satisfied via Decision).
Expect: exactly one new pin row for R2; rationale names the revision change;
the byte-identical satisfied clause carries `satisfied=true` +
`carried_from`; the Decision-satisfied clause keeps `via_decision`; changed
clauses start open.

## Scenario 5 — doctor seeded fault (FR-008)

Seed a pin row with duplicate ids / unparseable JSON / an orphan `goal_id`.
Expect: `check_contract_pins` FAIL naming the row and remedy; clean DB ⇒ OK.

## Guard rails that must stay green (SC-005)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q   # full suite
ruff check . && mypy
```

Zero-token idle tests (`FakeClaude.calls == 0`) and every existing
fail-closed done-gate case pass unmodified — if one fails, the change is
wrong, never the test.

## Live proof (post-deploy, Denys's button)

On the first active night after deploy, pick any goal that runs ≥2 done-gate
rounds and read its gate log: one `pinned contract … (N clauses)` line per
revision, every later round judging ids from that list. SC-004 is then read
off the scorecard over the following 14 active nights, alongside the
done-gate calibration eval set (companion workstream).
