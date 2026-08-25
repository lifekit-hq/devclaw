# Quickstart: validating the corrected scorecard

Prerequisites: `pip install -e ".[dev]"`; suite conventions per
`.claude/rules/testing.md` (private TMPDIR, `-n auto` default).

## 1. Unit/seeded validation (the definition tests)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_telemetry_scorecard.py \
  tests/test_goal_convergence_record.py \
  tests/test_pr_ledger_refresh.py
```

Expected: green. The seeded fixtures encode the audit-week shapes
(SC-001..SC-003): 3 task rows sharing 1 PR count once; a 6-round goal moves
first-pass by one goal; review-kind tasks move nothing; bench rows move only
`pr.bench`.

## 2. Zero-token / zero-subprocess guard

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_goal_tick.py \
  -k "idle or zero"
```

Expected: green — idle ticks make 0 cognition calls and 0 `gh` calls with
the ledger refresh wired in (SC-005).

## 3. Live read (against a running instance)

```bash
devclaw scorecard            # CLI render
# or over MCP: get_scorecard_metrics
```

Verify: output matches `contracts/scorecard-output.md`; `pr.state_as_of_ms`
is null-and-labeled-stale before the first cycle-report refresh, and
populated after one window close; `ratchet.checks` names each pass/fail and
`ratchet.pass` reflects the AND (SC-004).

## 4. Ground-truth spot check (SC-001)

Pick the window's `pr` counts, then by hand:

```bash
gh pr view <each distinct in-window PR url> --json state
```

Counts must match exactly (unknowns only where the repo is gone/unreachable).

## 5. Doctor

```bash
devclaw doctor   # instance checks include the two new tables + ledger freshness
```

Expected: new checks pass on a migrated instance; the seeded-fault tests
prove each check fires on a broken one.
