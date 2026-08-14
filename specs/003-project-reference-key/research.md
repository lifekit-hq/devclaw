# Phase 0 — Research: project reference key (P1)

Grounded against the current tree (2026-08-14). Every anchor was code-read.

## R1 — Where does resolution go? (the choke-point question)

**Decision**: Resolve `project_id → (workspace_dir, repo_url, override-knobs)` at
the **tool layer** (`server/tools.py`), at each of the four forwarding seams,
using the `registry` object already imported there (`tools.py:29`).

**The seams** (there is NOT one single seam — 4 sites across 2 downstream objects):
- `dispatch_task` → `queue.submit(...)` at `tools.py:83-92` (absorbs
  `implement_feature`/`fix_bug`/`review_repository`, which forward through it).
- `onboard` → its own `queue.submit(...)` at `tools.py:250-255`.
- `create_goal` → `goals.create_goal(...)` at `tools.py:770-775` (takes BOTH
  `workspace_dir` and `repo_url` today).
- `start_program` → `goals.create_goal(...)` at `tools.py:296-299` (deprecated
  alias).

**Rationale**:
- `registry` (`ProjectRegistry`) is already in scope at all four seams — no new
  wiring.
- `file_intake` (`tools.py:157-192`) is the exact existing precedent: a tool
  takes `project_id`, resolves against `registry`, and rejects an unknown
  project synchronously. Copy that shape.
- Pushing resolution DOWN into `queue.submit` / `goals.create_goal` would make
  the two service objects `project_id`-aware, widening the single-writer
  surface and touching the path-keyed joins. Keeping it at the tool layer means
  the services keep receiving a concrete `workspace_dir` exactly as today
  (FR-007 "populate, not replace").

**Alternatives considered**:
- *One helper in each service object* — rejected: two objects, more surface,
  breaks the "services stay path-keyed" invariant.
- *A new resolution middleware layer* — rejected as over-engineering; a single
  `registry.resolve_dispatch(project_id)` called at 4 sites is simpler and
  testable.

## R2 — What does resolution read, and how does an unknown id fail?

**Decision**: Add `ProjectRegistry.resolve_dispatch(project_id) -> ResolvedDispatch`
(or resolve inline via the existing `get`), reading `workspace_dir`, `repo_url`,
and the seven override knobs off the `Project` row. Unknown id → the tool raises
`ToolError(f"unknown project_id: {project_id!r} — register it first")`.

**Grounding**:
- `registry.get(project_id)` returns `Optional[Project]` (None on miss, does not
  raise) — `project_registry.py:315-320`. The `project_status` tool already uses
  exactly this guard shape (`tools.py:1327-1329`).
- The `Project` row carries everything resolution needs: `workspace_dir`,
  `repo_url`, and the knobs `automerge/merge_strategy/autodeploy/review_gate/
  verify_done/browser_gate_mode/sandbox_image` (`project_registry.py:94-126`).
- The override knobs do NOT need to be threaded through dispatch: today they are
  resolved at settle/merge time via `find_by_workspace_dir(workspace_dir)`
  (`merge.py:66,81`; `task_queue.py:1945,1958,1967`; `project_rollup` `:533`).
  Because resolution *populates* the resolved `workspace_dir` onto the row
  (FR-007), those joins keep resolving the same knobs unchanged. **So P1
  resolution only has to produce `workspace_dir` (+ `repo_url` for goals); the
  knobs ride the existing path join.** This shrinks P1 materially.

**Alternatives considered**:
- *Return knobs from `resolve_dispatch` and thread them through* — rejected for
  P1: unnecessary (path join already does it) and would touch 5 join sites.
  That is P3 (id-keyed joins), named-unsized.

## R3 — Where does preflight go, and what does it check?

**Decision**: A shared zero-token predicate — resolved workspace exists AND is a
git repo — invoked at the **two admission seams**, rejecting loud before any row
is claimed / any container:
- **Goal path**: in `goal/tick_dispatch.py` just before the existing
  `prepare_ws` call (`~L226`, before `L230`). Route failure through the
  already-wired `_block_on_prep_failure` → `mechanical:prep` breaker.
- **Direct path**: in `server/tools.py::dispatch_task`/`onboard` before
  `queue.submit` (or inside `TaskQueue.submit` before `create_task`, `:824`),
  because `submit(pump=True)` claims synchronously.

**Grounding for "reject loud, not late"**:
- Today the missing-workspace failure is `sandcastle.py:301-306` — checked at
  **launch**, only tests `exists()` + non-empty, is NOT a git check, and by then
  the task is a claimed `running` row (`task_queue.py:1235`). This is precisely
  the late/silent failure the preflight replaces.
- The `.git` predicate to reuse: `Path(workspace_dir) / ".git"` exists
  (`workspace.py:195`, `tick_guards.py:287`). For a stronger "is a working tree"
  check, `task_git.py:364` runs `git status --porcelain` — optional; the plain
  `.git` stat is enough for P1 and cheaper.

**Zero-token confirmation**: the entire admission→claim→launch chain is pure
Python + SQLite + git subprocess; the only LLM gates are settle-time
(`task_queue.py` review/browser gates). A fs/git preflight at these seams stays
on the zero-token side (`goal/tick.py:21`; representative guard
`tests/test_repo_brief.py:290`).

**Alternatives considered**:
- *Preflight inside `sandcastle._validate_workspace`* — rejected: that is
  launch-time, post-claim; too late (the whole point).
- *A single shared admission seam* — rejected: research shows the goal and
  direct paths admit at genuinely different points; a shared *predicate* (one
  function) called from both is the right factoring.

## R4 — Write-time path validation (container-side canonical)

**Decision**: Validate/normalize `workspace_dir` in `create()`/`update()`,
mirroring the existing `_validate_sandbox_image` write-choke
(`project_registry.py:278, 391`). Reject a path that does not resolve inside the
serving (container-side) process; normalize via the existing `_normalize_workspace`
(`:512-530`) so stored paths are canonical.

**Grounding**:
- Today paths are stored **verbatim** — no write-time normalization or
  validation (`create` writes `p.workspace_dir` raw at `:296`; `update` at
  `:370`). This is why the 2026-08-12 host-side `/srv/…` path was accepted
  silently.
- `_validate_sandbox_image` (`:75-82`) is the precedent for a write-time
  reject-with-reason.

**Open nuance (defer to tasks, not blocking)**: whether "resolves inside the
serving process" means an actual `os.path.isdir` at write time (rejects
registering a project before its clone exists) or a shape/prefix check. Lean:
**shape + normalize at write; existence is the dispatch-time preflight's job**
(R3) — so `register_project` before the clone exists is allowed, and dispatch is
what refuses to run against a still-missing workspace. Recorded for `/speckit-tasks`.

## R5 — Hard-cutover migration mechanics

**Decision**: A test fixture helper (`tests/`) that registers a throwaway
project row and returns its `project_id`, so the ~13 direct dispatch call sites
and the goal-creation tests migrate mechanically from
`workspace_dir=tmp_path` to `project_id=<helper(tmp_path)>`. Raw `workspace_dir`
param is deleted from the 4 tool signatures in the same PR.

**Grounding**: `register_project`/`create` already exist and are cheap; the
fixture mirrors the existing `seed_goal` helper style (`tests/goal_fakes.py`).
The dry-run tools (`_dry_goal`, `tools.py:557-582`, `workspace_dir="/dev/null"`)
are internal and do NOT take a caller `project_id` — they stay as-is
(harness-internal, special-cased per clarify Q1).

**Risk**: a wide diff (~90 files touch `workspace_dir`, though far fewer are
dispatch params). Mitigation: the fixture keeps each edit shallow + identical;
the review gate is out of the chain under `trust` and this is a Denys-reviewed
self-repo PR. Flag the exact touched-file count in the PR body (Constitution VI —
no silent bounded coverage).
