# Pre-amputation baseline (FR-021)

**Recorded**: 2026-08-20
**Commit**: `8fe277f` — `chore(main): release 0.3.0 (#568)`
**Tag**: `pre-amputation-v0.3.0`

## Test suite

```
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
1990 passed, 4 skipped in 61.02s
```

Import path verified: `/Users/dsdevq/Projects/devclaw/devclaw/__init__.py`.

**Post-cut gate**: the amputated tree must be green with **no failures and no
errors**. The passed-count will DROP (the cut removes ~3,300 lines of tests
belonging to deleted mechanisms) — a lower count is expected here and is not the
signal. The signal is: zero failures, and every removed test traceable to a
removed mechanism in the PR inventory (FR-020).

## Tree size

| Surface | Lines | Files |
|---|---|---|
| `devclaw/` | 32,744 | 109 |
| `runner/` | 2,374 | 16 |
| `tests/` | 37,403 | 177 |

MCP tools exposed: **47** (`grep -c '@mcp.tool' devclaw/server/tools.py`).

## Live instance at baseline

| | |
|---|---|
| Deployed sha | `468b072` (v0.2.0) — two commits behind `main` |
| Goals | 18 total · 0 running · 1 needs-you · 6 done · 11 cancelled |
| Clean cycles | 4 clean / 19 total (11 idle) |
| Last cycle | 2026-08-19 — CLEAN, settled 8 (6 done, 2 failed) |
| Problem fingerprints | 89 |
| `donegate_churn` occurrences | 0 |
