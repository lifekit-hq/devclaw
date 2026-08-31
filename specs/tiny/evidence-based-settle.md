# Tiny Spec: evidence-based settle (issue #565)

## What
Before failing a no-result termination (timeout, transport loss, worker death),
the settle path inspects the workspace for committed + verifiable work. Committed
work that passes `verify_cmd` is salvaged through the normal gate+delivery
pipeline; work that exists but fails verify gets a wip snapshot pushed to the
goal branch so the next attempt can inspect rather than redo.

## Context
Night 2026-08-18, task `0b5e09e0`: the worker committed real, test-green work at
01:24, then an ACP transport timeout tore the sandbox at 02:10 and devclaw
settled `failed` — wiping the committed increment on the next dispatch. Root
cause: "what happened" was derived from a response message over a fragile pipe
instead of from the workspace itself. Doctrine: TRUST THE INPUT, VERIFY THE OUTPUT.

## Requirements
1. On `asyncio.TimeoutError` (and only on it — no-result termination), before
   settling `failed`, check `git log <pre_run_sha>..HEAD --oneline` in the
   workspace.
2. No commits → settle `failed` as today (existing message unchanged).
3. Commits present + no `verify_cmd` → push an interrupted wip (commit any
   uncommitted work + push to origin so it survives `prepare_workspace`), settle
   `failed` with a note that work was preserved.
4. Commits present + `verify_cmd` runs red → same as (3), include verify output
   tail in the failure reason.
5. Commits present + `verify_cmd` runs green → run the FULL gate pipeline
   (verify → materialize → integrity → scope → [review if strict] → browser);
   if ALL gates pass → deliver via the existing `defer_done` / `mark_done` path
   with `salvaged: true` in the result; if any gate fails → settle `failed`.
6. Fail-closed stance is untouched: nothing ships without the verify evidence
   AND the gate chain. A salvage crash → settle `failed`.

## Plan
Files touched:
- `devclaw/task_git.py` — add two sync helpers:
  - `_check_no_result_evidence_sync(host_dir, base_sha, verify_cmd)` → dict
  - `_push_interrupted_work_sync(host_dir, task_id)` → str reason
- `devclaw/queue/settle.py` — add async wrappers (module globals, patchable by
  tests), replace the `asyncio.TimeoutError` handler in `_run_and_settle`

## Tasks
- [x] Write tinyspec
- [ ] Add helpers to task_git.py
- [ ] Add async wrappers + timeout handler to settle.py
- [ ] Add named regression test to tests/test_task_retry.py
- [ ] Run suite + lint

## Done-When
`test_no_result_termination_with_green_verify_salvages_instead_of_wiping` passes;
a no-commit timeout still settles plain `failed`.
