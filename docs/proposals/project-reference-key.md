# Proposal — project reference key: the registry becomes the single source of truth for dispatch

- **Status:** **DRAFT** — 2026-08-13. Captured from the night-2026-08-12
  post-mortem; all `[OPEN]`s below are mandatory before LOCK. **No code before
  lock** (tool-surface change, spec-lifecycle).
- **Date opened:** 2026-08-13 · **Authors:** Denys + Claude
- **Relates to / does not restate:** the layer map + single-writer invariant in
  `CLAUDE.md`; ADR 0011 (branch-target delivery seam) for the dispatch-param
  precedent; `harden-dispatch-workspace-prep` worktree (this gap half-known).

## The incident that surfaced the class

Night 2026-08-12: all three devclaw self-fix dispatches (#491/#329/#501) died
instantly — `cannot change to '/srv/devclaw/workspaces/devclaw': No such file
or directory`. The registry row carried a **host-side** path from before the
container migration (the container sees `/var/lib/devclaw/workspaces/…`), plus
a stale `repoUrl` (dsdevq/ instead of lifekit-hq/). A fourth attempt used a
path the waiter invented outright. Same class as the
`project_registry_link_stale` vault note: registry rows rot silently because
nothing load-bearing reads them.

## Ground truth (code-read 2026-08-13)

- The registry is **not on the dispatch path at all**. `dispatch_task` /
  `implement_feature` / `fix_bug` / `create_goal` take a raw `workspace_dir`
  string (`server/tools.py:31-91`); the only check is non-emptiness. Callers
  can invent any path; it is accepted and fails later, deep in the engine
  (`sandcastle.py:268` at sandbox launch).
- Registry reads are **override-knob and status lookups only** (automerge,
  merge_strategy, rollups) — and they join **by normalized workspace path**,
  never by id (`task_queue.py:1901+`, `goal/merge.py:66`,
  `project_registry.py:533`). `project_id` is a display/CRUD key.
- `register_project`/`update_project` store the path **with zero validation**.
- Auto-prep exists **only on the goal path** (`engine/workspace.py:195`
  clones from `repo_url`; `mechanical:prep` self-heal with damped backoff).
  The direct-dispatch path never clones and takes no `repo_url`.

## Direction

Register once, reference by key, resolve server-side:

1. **`project_id` becomes the dispatch reference key.** The dispatch tools
   (and `create_goal`) accept `project_id`; devclaw resolves `workspace_dir`,
   `repo_url`, and the override knobs from the registry row at the single
   choke point every caller crosses (`tools.py:79`), rejecting unknown ids
   synchronously with a `ToolError`.
2. **Dispatch-time preflight.** The resolved workspace must exist and be a
   git repo — checked at admission, not at sandbox launch. A missing
   workspace either auto-prepares from the row's `repoUrl` (goal-path
   `prepare_ws` reused) or rejects loudly; never a claimed task that dies
   at start.
3. **Trustworthy at write time.** `register_project`/`update_project`
   validate/normalize the path (and, when the workspace is absent but a
   `repoUrl` is present, may prepare it) so the stored value is dependable at
   resolution time.
4. **Resolution populates, not replaces.** The resolved `workspace_dir` is
   still persisted onto task/goal rows — every internal join
   (`find_by_workspace_dir`, rollups, override resolution) is path-keyed and
   keeps working unchanged in P1.

## Slices

- **P1 — the reference key + preflight** (~2 PRs): `project_id` param on
  `dispatch_task`/`create_goal` (+ forwarding aliases), server-side
  resolution, unknown-id rejection, workspace-exists preflight at admission,
  write-time validation in the registry tools. Raw `workspace_dir` stays as a
  deprecated-but-working alternative.
- **P2 — direct-path auto-prep**: missing workspace on a registered project
  auto-clones from `repoUrl` (goal-path `prepare_ws` + `mechanical:prep`
  semantics reused on the direct path).
- **P3 — id-keyed joins**: migrate the internal workspace-path joins to
  `project_id` so the path becomes a resolved detail, not a join key.
  Named-unsized.

## [OPEN] — mandatory before LOCK

- **[OPEN-1] Deprecation path for raw `workspace_dir`.** Grace period with
  both accepted (waiter flows and tests migrate gradually), or hard cutover
  once the OpenClaw waiter prompt is updated? Who owns updating the waiter?
- **[OPEN-2] Unregistered/ad-hoc dispatch.** Keep a raw-path escape hatch
  forever (self-fix goals, `/dev/null` dry-runs, one-off human dispatches),
  or require a registry row for everything and special-case the harness's
  own internals?
- **[OPEN-3] Preflight failure verb.** Missing workspace at dispatch:
  reject the tool call (caller retries after fixing), or accept + block
  `mechanical:prep` + auto-heal once the clone appears (goal-path
  semantics)? One answer for both goal and direct paths, or per-path?
- **[OPEN-4] Container/host path duality.** The incident's root cause was a
  host-perspective path stored for a container-perspective consumer. Does the
  registry store canonical container-side paths only (validated inside the
  serving process), or grow an explicit mapping? (Today: container-side only,
  by convention — the convention was violated silently.)
