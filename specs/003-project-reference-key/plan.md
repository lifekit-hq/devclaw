# Implementation Plan: Registry as the single source of truth for dispatch (project reference key)

**Branch**: `feat/project-reference-key` | **Date**: 2026-08-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-project-reference-key/spec.md`

**Scope of THIS plan**: P1 only (reference key + preflight + write-time
validation + hard cutover). P2 (direct-path auto-prep) and P3 (id-keyed joins)
are out of scope and named in the spec.

## Summary

Make the registry row the authoritative source of dispatch facts. Every dispatch
entrypoint stops taking a raw `workspace_dir` and takes a `project_id` that
devclaw resolves — server-side — into `workspace_dir` + `repo_url` + override
knobs, at the tool layer where `registry` is already in scope. An unknown
`project_id` is rejected synchronously (`ToolError`, zero task row, zero engine
work). At admission (before any row is claimed / any container), a **preflight**
asserts the resolved workspace exists **and is a git repo** — closing the gap
that today fails late and silently at sandbox launch. `register_project` /
`update_project` validate the stored path (canonical container-side) so the row
is trustworthy at read time. Per the clarify session this is a **hard cutover in
a single PR**: raw `workspace_dir` is removed and every in-repo call site + test
+ the OpenClaw waiter prompt migrate to `project_id` together.

## Technical Context

**Language/Version**: Python 3.11 (existing devclaw runtime)

**Primary Dependencies**: FastMCP (`ToolError`), SQLite (`project_registry`,
`state_store`), git subprocess (`prepare_workspace`, `task_git`)

**Storage**: `devclaw.db` — `projects` table (registry rows) + task/goal rows.
No schema change in P1 (resolution reads existing columns).

**Testing**: pytest, fully stubbed (`FakeClaude`/`FakeEngine`, `tests/goal_fakes.py`).
Zero-token guard tests (`FakeClaude.calls == 0`) are load-bearing.

**Target Platform**: Linux container on the VPS (serving process is container-side).

**Project Type**: Agentic-loop harness — layers 1 (MCP `server/`) → 2 (`goal/`)
→ 4 (`task_queue.py`, `engine/`). This change lives at layers 1–2 (the dispatch
choke points), not the worker (layer 5).

**Performance Goals**: Resolution + preflight are O(1) SQLite read + one
filesystem `.git` stat — sub-millisecond, no LLM, no container.

**Constraints**: Must not add any tick-path/idle LLM call (Constitution III);
must not break the workspace-path-keyed override/rollup joins (FR-007); must
fail loud on unknown id / missing workspace (Constitution VI).

**Scale/Scope**: Blast radius of the hard cutover — **~90 test files** and
**~47 source files** reference `workspace_dir`; the dispatch *param* call sites
are far fewer (4 tool seams + ~13 test call sites use the dispatch tools
directly). The migration is mechanical but wide.

## Constitution Check

*GATE: must pass before Phase 0. Re-checked after Phase 1 (below).*

| Principle | Status | Note |
|---|---|---|
| I. OAuth only | ✅ PASS | No cognition/key path touched. |
| II. Model-agnostic worker | ✅ PASS | Change is layers 1–2; worker untouched. |
| III. Zero-token idle | ✅ PASS | Resolution = SQLite read; preflight = `.git` stat + optional `git` subprocess. Both sit on the zero-token side of the dispatch seam (confirmed: `goal/tick.py:21`, dispatch path makes no `ClaudeCaller` call). New guard test asserts `FakeClaude.calls == 0` across a by-id dispatch + a rejected unknown id + a preflight rejection. |
| IV. Single writer to state | ✅ PASS | TaskQueue still the only task-row writer; registry stays the project-row owner. Resolution *reads* the registry and *populates* the resolved `workspace_dir` onto the task/goal row — it does not introduce a second writer. |
| V. Verification fails closed | ✅ N/A | No gate semantics touched. |
| VI. Loud failure | ✅ PASS (this IS the feature) | Unknown id → synchronous `ToolError`; missing/non-git workspace → loud admission reject with an actionable reason, replacing the silent late-launch timeout (`sandcastle.py:302`). |
| VII. Fix the class | ✅ PASS | Changes *where dispatch gets its facts*, not either incident's row. |

**No invariant change required.** The feature strengthens IV + VI. If Phase 1
surfaces an invariant conflict, amend `.specify/memory/constitution.md` in the
same PR (none found).

## Project Structure

### Documentation (this feature)

```text
specs/003-project-reference-key/
├── spec.md              # clarified spec (done)
├── plan.md              # this file
├── research.md          # Phase 0 — grounded decisions (done)
├── data-model.md        # Phase 1 — the resolved-dispatch-request shape (done)
├── contracts/
│   └── dispatch-tools.md # Phase 1 — the tool-signature contract change (done)
├── quickstart.md        # Phase 1 — how to validate P1 end to end (done)
└── tasks.md             # Phase 2 — /speckit-tasks (NOT created here)
```

### Source Code (files this feature touches)

```text
devclaw/
├── server/tools.py            # 4 dispatch seams → take project_id, resolve via registry
│                              #   dispatch_task(L83), onboard(L250), create_goal(L770), start_program(L296)
│                              #   reuse the file_intake precedent (project_id → resolve → reject unknown)
├── project_registry.py        # + resolve_dispatch(project_id) -> (workspace_dir, repo_url, knobs)
│                              # + write-time path validation in create()/update() (mirror _validate_sandbox_image)
├── task_queue.py              # preflight seam for the DIRECT path (before submit/claim, L823-841)
├── goal/
│   ├── tick_dispatch.py       # preflight seam for the GOAL path (before prepare_ws, L226-234)
│   └── engine.py              # unchanged (submit(pump=False) still receives a resolved workspace_dir)
└── engine/workspace.py        # unchanged in P1 (its .git/reject-or-clone logic is the P2 reuse target)

tests/                         # migrate all dispatch call sites off workspace_dir → project_id
                               # + new named regression tests (see quickstart.md)

# OUT OF REPO (named lockstep release step, Denys-owned):
#   OpenClaw waiter prompt (dsdevq/lifekit-stack) → dispatch by project_id
```

**Structure Decision**: Resolution is inserted at the **tool layer**
(`server/tools.py`), not pushed into the two downstream service objects
(`queue`, `goals`). Rationale in research.md R1 — `registry` is already in scope
at all four seams, the `file_intake` precedent already does exactly this shape,
and keeping the service objects unaware of `project_id` preserves the single-
writer boundary and the existing path-keyed joins untouched (FR-007). Preflight
sits at the **admission seam of each path** (two: goal path before
`prepare_ws`; direct path before `submit`'s synchronous claim) — the earliest
zero-token point before a row is claimed.

## Complexity Tracking

*No constitution violations — this section intentionally minimal.*

| Item | Why it looks complex | Why it is not a violation |
|---|---|---|
| Two preflight seams (goal + direct) | One might expect a single seam | The two admission surfaces are genuinely distinct (goal path preps before the row exists; direct path claims synchronously in `submit`). Research R3 confirms there is no shared earlier choke point. Both reuse the SAME `.git` predicate + reject shape, so the *logic* is single-sourced even though it is called from two places. |
| Big-bang test migration (~90 files touched) | Large single diff | Denys's explicit ruling (clarify Q2): hard cutover, no deprecation window. Kept mechanical (a fixture helper that registers a throwaway project and returns its id — see quickstart) so the diff is wide but shallow. |
