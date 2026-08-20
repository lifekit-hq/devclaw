# Quickstart: validating the amputated build

## Prerequisites

Worktree on `refactor/speckit-native-amputation`. Verify the import path
resolves to the WORKTREE before any test run — the shared venv's editable
install points at the main checkout:

```bash
cd <worktree>
.venv/bin/python -c "import devclaw; print(devclaw.__file__)"   # must print the worktree path
```

## 1. Suite

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

**Expected**: zero failures, zero errors. The passed-count is **lower** than the
`1990` baseline — ~2,950 test lines belong to deleted mechanisms. Count parity
is NOT the gate; see `baseline.md`.

## 2. The vocabulary is gone

```bash
grep -rn "program" devclaw/ --include=*.py | grep -vi "programming\|program_id IS NULL"
grep -rn "trend_\|repo_brief\|elicitation\|claude_sdk\|eval_judge" devclaw/ | grep -v "\.pyc"
```

**Expected**: no dispatch path, model, prompt or tool references either concept.
The only surviving `program_id` reference is the retained
`list_pending_standalone` guard.

## 3. MCP surface

```bash
grep -c "@mcp.tool" devclaw/server/tools.py     # expect 42 (was 47)
```

**Expected**: `get_program`, `list_programs`, `cancel_program`, `review_trends`,
`scope_grill` gone. Every dispatch name still present as deprecated sugar.

## 4. The two behavior changes

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q -k "advance_brief or delivery_title or pending_standalone"
```

**Expected**: the named regression tests pass —
- a brief with any prefix is still detected as an advance brief
- no dispatch text can reach a PR title or body
- a pending row with a populated `program_id` is never claimed

## 5. Zero-token guard (load-bearing)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_goal_tick.py
```

**Expected**: green. `FakeClaude.calls == 0` on idle and blocked paths. If one
of these fails, the change is wrong — never the test.

## 6. End-to-end, stubbed

```bash
DEVCLAW_ENGINE=stub .venv/bin/python evals/run_all.py
```

## 7. Real pipeline (before the night run)

Follow `docs/runbooks/live-shakedown.md` — L1 single task at minimum. The
stubbed suite cannot catch a broken sandbox launch.

## Rollback

```bash
git checkout pre-amputation-v0.3.0
```

Code-only, and complete: this PR alters no schema.
