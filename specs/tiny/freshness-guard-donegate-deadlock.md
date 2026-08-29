# Fix freshness-guard + full-scope done_when deadlock (issue #726)

## What
When all referenced issues are closed, the dispatch-boundary freshness guard
proposes done without dispatching a worker. If the done-gate refuses
(`off_track`), the next tick re-enters the same path (issues are still closed),
proposes done again, and the cycle repeats until `donegate_churn` fires (3
rounds). Zero workers ever dispatch; the goal can never make progress.

## Context
The freshness guard's premise — *all referenced issues closed ⇒ work no longer
needed* — is false when an issue was closed by a **partial** implementation
(e.g. an intermediate PR with `Closes #N`). The done-gate correctly refuses
because the contract is genuinely unmet; the guard then removes the only path
that could satisfy it. Each refused round costs one `review_repository` call
(~12 min), and three rounds burn ~36 min before `donegate_churn` parks the goal.

Root cause: `_handle_long_lived_advance` in `devclaw/goal/tick.py` — the
"all closed → propose done" branch never checks whether the gate already refused,
so it re-proposes on every tick without dispatching.

## Requirements
1. When all issues are closed AND `donegate_rounds == 0`: propose done (first
   pass — the out-of-band work may have genuinely satisfied the contract).
2. When all issues are closed AND `donegate_rounds > 0` (gate has already
   refused): dispatch a worker instead of re-proposing. The contract is the
   authority; the issue closure is not proof of satisfaction.
3. The worker brief in the fallback-dispatch case carries all closed issues as
   "do NOT work on" context so the worker doesn't re-open or re-work them.
4. One named regression test pinning the new behavior.

## Plan
Single change in `devclaw/goal/tick.py::_handle_long_lived_advance`:
- Wrap the existing "all closed → `_open_done_gate`" call in
  `if base.donegate_rounds == 0:`.
- In the else branch: log the override reason, set
  `issue_context = _issue_ref.render_issue_context([], snaps)` (all dropped),
  and fall through to the existing `_dispatch_action` call.

Regression test in `tests/test_issue_ref_freshness.py`.

## Tasks
- [x] Write tinyspec
- [x] Fix `devclaw/goal/tick.py`
- [x] Add regression test in `tests/test_issue_ref_freshness.py`
- [x] Run ruff + mypy + pytest

## Done-When
- `test_all_refs_closed_after_gate_refusal_dispatches_worker` passes green.
- The existing `test_all_refs_closed_proposes_done_with_zero_worker_sessions`
  still passes (first-pass behavior unchanged).
- Full suite green, ruff clean, mypy clean.
