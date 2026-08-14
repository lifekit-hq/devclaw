# Quickstart — validating Review-Gate Repositioning

How to prove the feature works, end to end. Runs against the fully-stubbed suite
(no docker, no `claude`).

## Prerequisites

```bash
pip install -e ".[dev]"
# In a worktree, verify the import path FIRST (tests/rules):
.venv/bin/python -c "import devclaw; print(devclaw.__file__)"   # must print the worktree path
```

## Scenario 1 — trust mode skips the per-increment review entirely (FR-001, SC-001)

Drive a trust-mode task to settlement with the review reviewer stubbed to **crash
on any call**; it must settle `done` and deliver, and the reviewer stub must show
**zero invocations**.

- Fixture: a goal with `strictness="trust"`, a passing verify result, a
  `FakeClaude`/review stub that raises if called for a review.
- Assert: task settles `done`; a delivery/PR is produced; the review stub's call
  count is `0`; `FakeClaude.calls` for the review path is `0`.
- Named test: `test_trust_mode_skips_per_increment_review_even_when_reviewer_crashes`.

## Scenario 2 — strict mode is byte-identical to today (FR-002, SC-003)

Run the existing review-gate suite against a `strictness="strict"` goal — every
assertion that holds today must hold unchanged (findings block, crash fails
closed with no agent retry, degradation ladder engages on oversized diffs).

- Command: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest tests/test_review_gate*.py -q`
- Assert: all green with no test modified for strict-mode behavior.
- Named test: `test_strict_mode_review_gate_runs_and_blocks_as_before`.

## Scenario 3 — always-hard gates unchanged under trust (FR-003)

A trust-mode task whose **verify** (tests) fails must still fail; a **test-integrity**
violation must still block. The dropped gate is only the adversarial diff review.

- Named test: `test_trust_mode_still_fails_closed_on_verify_and_integrity`.

## Scenario 4 — the PR discloses no review ran (FR-004)

A trust-mode delivery's PR surface states plainly that no per-increment agent
review ran, so the human merge review knows it is the first semantic reader.

- Named test: `test_trust_delivery_pr_discloses_no_per_increment_review`.

## Scenario 5 — done-gate untouched (FR-008) and zero-token guard intact (Principle III)

- The goal-level `review_repository` done-check still runs in both modes.
- Every existing `FakeClaude.calls == 0` idle/blocked-path guard test stays green.
- Command: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` (full suite; count
  must not drop below the pre-change baseline of 2124 passed).

## Full-suite gate (before PR)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
```
Green baseline: **2124 passed, 5 skipped** (as of 2026-08-14). A lower pass count
than baseline means something broke even if the new tests pass.
