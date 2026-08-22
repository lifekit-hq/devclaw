# Implementation Plan: One definition of the change

**Branch**: `feat/013-materialize-change` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-materialize-change/spec.md`, issue [#630](https://github.com/lifekit-hq/devclaw/issues/630)

## Summary

Insert ONE host-side step — **materialize** — between "the agent stopped" and
"the gates judge", and make every downstream consumer read its output instead of
recomputing its own view of the change.

Materialization stages everything left in the workspace and (only when there is
something to stage) writes it into a commit, exactly the way delivery does today.
It returns a `ChangeSet`: `(status, base_sha, head_sha, diff, agent_authored)`.
The gates, the change-size projection, the post-run advisory checks and delivery
all consume that one object. Two consumers naming the same `head_sha` cannot
disagree.

## Technical Context

**Language/Version**: Python 3.11 (host), bash (worker hooks)

**Primary Dependencies**: git CLI, stdlib `subprocess`/`asyncio`

**Storage**: `devclaw.db` — `tasks.pre_run_sha` already exists; the post-run
counterpart rides the task **result JSON** (`result["change"]`), not a new
column. Rationale: the span is per-ATTEMPT run artifact, not durable task
identity, and `devclaw/state_store/**` is being churned by PRs #634/#638.

**Testing**: pytest, fully stubbed (`tests/test_task_change.py`,
`tests/test_materialize_gate.py`, `tests/test_change_advisories.py`, plus edits
to `test_task_retry.py`, `test_delivery*.py`, `test_runner_skills.py`,
`test_browser_gate_doctrine.py`)

**Target Platform**: Linux host process (layer 4), any engine (sandcastle /
host / stub)

**Project Type**: agentic loop — this change is entirely inside layers 4 (queue
+ delivery) and 5 (worker hooks/skills)

**Constraints**: zero LLM calls added; no work added to any idle/blocked tick
path; must not restructure the gate chain (PR #637 stacks a `_ScopeGate` into it)

**Scale/Scope**: ~8 product files, ~5 doc files, ~6 test files

## Constitution Check

| Principle | Effect of this plan |
|---|---|
| **I — OAuth only** | untouched; materialization is pure git, no cognition |
| **II — Model-agnostic worker layer** | STRENGTHENED. The definition of the change leaves the worker layer entirely: `runner/hooks/post-run.sh` stops computing a diff, `runner/skills/_writes-code/90-commit.md` stops being load-bearing. `runner/skills/` remains the one home for worker instructions; nothing is copied. |
| **III — Zero-token idle** | untouched. Materialization runs only on the settle path of a task that actually ran, behind the verify gate's short-circuit. `FakeClaude.calls == 0` guards stay green. |
| **IV — Single writer to state** | this is the point: one component now owns the definition of the change. |
| **V — Verification fails closed** | STRENGTHENED. A span that cannot be determined is a new always-hard gate failure (`materialize`), never an empty diff handed to a gate. |
| **VI — Loud over silent** | an undeterminable span, a non-repo workspace, and a post-materialization drift at delivery are all explicit outcomes. |
| **VII — Fix the class** | the fix is the mechanism, not the observed file. The rejected one-liner (`git add -N`) is recorded in the spec. |

No amendment required.

## Design

### The one artifact

```python
# devclaw/task_change.py
@dataclass(frozen=True)
class ChangeSet:
    status: str            # "change" | "no_change" | "no_repo" | "error"
    base_sha: str = ""     # the task's pinned pre-run reference
    head_sha: str = ""     # the post-run reference — NEW
    diff: str = ""         # git diff base_sha head_sha
    reason: str = ""       # populated for "error" / "no_repo"
    agent_authored: bool = False  # the agent itself wrote the commit at head
    materialized: bool = False    # devclaw created or amended a commit
```

`materialize_worktree_sync(host_dir, base, *, task_id, message)`:

1. `rev-parse --is-inside-work-tree` — not a repo ⇒ `no_repo`.
2. `agent_authored = rev-list --count base..HEAD > 0` — measured BEFORE staging,
   so it answers "did the agent write a commit", not the `ahead > 0` proxy
   delivery uses today (which is true in goal-branch mode for prior increments
   the agent never touched).
3. `status --porcelain`; if dirty: `git add -A`, then
   * `commit --amend --no-edit` when `agent_authored` and HEAD is not pushed —
     byte-identical to delivery's existing fold-leftovers-in rule, so a worker
     that committed properly ends with the same single commit it does today;
   * else `commit -m <message>` with the same message delivery composes today
     (`_resolve_title(planner_title=…, agent_msg=None, …)`).
   Any non-zero git ⇒ `error`.
4. `rev-parse HEAD` ⇒ `head_sha`.

`_capture_change` (async, in `task_queue`) wraps it, then asks the module-global
`_git_diff(host_dir, base, head)` seam for the unified diff of the range.
`None` from that seam means "git could not answer": `error` in a repo, `no_repo`
otherwise. It never raises.

### Where it runs

`_run_and_settle`, lazily, behind `GateInput`. `GateInput` gains `change_fn` +
a memoised `async change()`; `diff()` now delegates to it. The pipeline gains
`_MaterializeGate` between `verify` and `test_integrity`:

```
verify → materialize → test_integrity → [scope (#637)] → [review] → browser
```

* after `verify` so a verify failure still short-circuits before any git runs
  (the property `gate_pipeline`'s docstring promises, and a test pins);
* before every diff-reading gate, so no gate can ever see a pre-materialization
  diff;
* `"materialize"` joins `ALWAYS_HARD` — an undeterminable span is not a finding
  to weigh at the merge boundary.

### Publication

`deliver_change` gains `judged_head` + `agent_authored`. When `judged_head` is
supplied it:

* verifies HEAD is exactly `judged_head` and the tree is clean, failing LOUD on
  drift (`delivery_failed` treats it as a real failure — the task does not
  settle `done`);
* **skips the `git status` → `git add -A` → commit/amend block entirely** — the
  second computation is gone, not merely in agreement;
* uses `agent_authored` instead of the `ahead > 0` proxy to decide whether to
  read the worker's own commit subject for the PR title.

### No change / cannot determine

| outcome | task | delivery | goal layer |
|---|---|---|---|
| `change` | as today | publishes the judged head | delivered increment |
| `no_change` (code-writing kind) | settles `done`, `result["no_change"] = True` | **not attempted** | `delivered = 0` ⇒ feeds the no-progress watchdog |
| `no_change` (read-only kind) | settles `done` | not attempted (already the case) | unchanged — still a delivery |
| `no_repo` | settles as today | fails as it does today (`workspace is not a git repository`) | unchanged |
| `error` | `materialize` gate fails CLOSED | never reached | failed action |

### Retired compensations (US3)

* `_git_diff_sync`'s three-way fallback ladder (`diff <base>` → `diff` →
  `diff --cached`) — it existed to guess what state the agent left the tree in.
  Replaced by one two-point `git diff <base> <head>`; a failure is reported, not
  swallowed.
* The retry-isolation `git reset --hard` + `clean -fdx` between attempts
  (FR-012/FR-013): the reset existed so the gates diffed a clean base. The base
  is now pinned and every attempt is materialized and judged in FULL against it,
  so the reset only destroys work the agent got mostly right.
* All three diff-reading checks in `runner/hooks/post-run.sh` move host-side to
  `devclaw/quality/change_advisories.py`, reading the one artifact. The
  `.devclaw-pre-head` sidecar (the worker layer's own copy of the base) goes with
  them.
* `runner/skills/_writes-code/90-commit.md` is demoted to what it should always
  have been: guidance on writing a good message and naming judgment calls.

## Assumptions (clarify skipped by owner direction)

1. **`no_repo` is a distinct outcome from `error`.** The spec's edge case lumps
   "not a repository" with "repository commands fail". They are separated here
   because the *divergence this spec closes cannot exist* without a repository:
   delivery already fails loudly on a non-repo (`workspace is not a git
   repository`, not a benign error), so nothing can ship unjudged. Failing every
   non-repo task closed instead would change ~176 stubbed test workspaces that
   are paths rather than repos — far more than the defect. It is still reported
   out loud (`result["change"]["status"] == "no_repo"` + a stderr line), never as
   a silent empty change.
2. **The post-run reference lives on the task result, not a new `tasks` column.**
   It is a per-attempt artifact and `state_store/**` is contended by #634/#638.
   FR-004 asks for "a stable reference recorded on the task"; `result_json` is
   on the task row.
3. **The materialization commit reuses delivery's own title resolution** so a
   worker that commits nothing still gets the identical PR title, branch slug and
   commit message it gets today.
4. **`no_change` is stamped only for code-writing kinds.** `review_repository`
   writes a report and legitimately changes nothing (FR-011); marking it
   no-progress would break the watchdog for verification-heavy goals.
5. **Bounded-view honesty (FR-009)** is satisfied by counting binary files
   separately in `diff_stats` (`{"files", "insertions", "deletions", "binary"}`)
   — a binary file contributes 0 line counts in a unified diff, so the projection
   says so instead of under-reporting silently.

## Interaction with in-flight work

* **PR #637** (`_ScopeGate`, spec 010 FR-103) reads `gi.diff()`. After this
  change that call returns the materialized span, so a `[P]` increment can no
  longer escape its declared scope by simply not committing the out-of-scope
  files. The chain insertion point is *before* `test_integrity`, #637's is
  *after*, so the conflict is one adjacent line in the `gates` list.
* **PRs #634/#638** (`devclaw/goal/store/**`, `goal/state.py`) — untouched here.
* **PR #636** (`devclaw/telemetry.py`, `devclaw/loom/trace.py`) — untouched here.

## Project Structure

```text
devclaw/
├── task_change.py                    NEW — the one answer to "what changed"
├── task_git.py                       _git_diff_sync → two-point range, ladder gone;
│                                     _git_reset_clean_sync deleted
├── task_queue.py                     _capture_change seam, _MaterializeGate,
│                                     no-change routing, advisory call, retry reset gone
├── delivery/__init__.py              judged_head / agent_authored; discovery removed
├── quality/gate_pipeline.py          GateInput.change() + change_fn
├── quality/gate_policy.py            "materialize" ∈ ALWAYS_HARD
├── quality/change_advisories.py      NEW — the relocated post-run checks
├── goal/models.py                    PollResult.no_change
├── goal/engine.py                    _no_change projection
└── goal/tick_settle.py               delivered = done AND NOT no_change
runner/
├── hooks/pre-run.sh                  .devclaw-pre-head write removed
├── hooks/post-run.sh                 diff-reading checks removed
└── skills/_writes-code/90-commit.md  demoted to message guidance
```
