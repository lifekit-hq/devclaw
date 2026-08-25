# Quickstart: validating Goal-as-Pointer

Prerequisites: `pip install -e ".[dev]"`; suite conventions per
`.claude/rules/testing.md`.

## 1. Doorway validation matrix (seeded, no network)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_goal_issue_refs.py
```

Expected: every refusal row of `contracts/create-goal.md` covered by a named
test asserting BOTH the refusal and its message contents (rule + input +
fixing verb); the within-budget ready-ref happy path creates; the issue-less
lane is regression-pinned byte-compatible.

## 2. Freshness semantics

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_issue_ref_freshness.py tests/test_done_when_scenarios.py
```

Expected: edit-then-dispatch carries post-edit body (SC-001); closed-issue
item skips with zero worker sessions (SC-002, the #684-class fixture);
unfetchable ref blocks human-gated; live-at-eval scenarios honored after a
mid-goal edit; scenario-absence blocks the gate round, never evaluates empty.

## 3. Zero-token / zero-fetch idle guard

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_goal_tick.py \
  -k "idle or zero"
```

Expected: green — `FakeClaude.calls == 0` AND `FakeIssueFetcher.calls == 0`
on idle/blocked paths (SC-004).

## 4. Live smoke (dev instance, real gh)

1. Grade a test issue ready on a sandbox repo; give it an `## Acceptance`
   section.
2. `create_goal(..., issues=[N])` with a one-line objective → accepted.
3. Same call with 2,000 chars of objective → refused; follow only the verbs
   the message names to reach acceptance (SC-006).
4. Close the issue; trigger a dispatch window → no worker session spent,
   goal log names the skip.

## 5. Full gate

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy
devclaw doctor   # project check: referenced-goal records parse
```
