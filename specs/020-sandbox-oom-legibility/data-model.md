# Data Model: spec 020

## New persisted fields

### `goal_status.envcap_redispatches` (INTEGER, default 0)

The FR-002a budget: how many adapted re-dispatches this goal has spent on the
environment-cap class.

- **Increment**: when the goal dispatches an increment whose brief carries the
  OOM-adapted failure context (the class recurred and budget remained).
- **Reset to 0**: on a productive settle (same site as `heal_attempts`,
  `tick_settle.py` — a shipped increment proves the environment now fits).
- **Gate**: a new environment-cap failure with `envcap_redispatches >= 1`
  blocks the goal instead of re-dispatching.
- Lockstep seams (all five, per the goal-state convention): `goal/models.py`
  dataclass, `goal/state.py` DDL, `goal/state_status.py` upsert + row read,
  `goal/store/status.py` frontmatter dict, `goal/store/view_migration.py`.

### `projects.sandbox_memory`, `projects.sandbox_cpus` (TEXT, nullable)

Per-project sizing overrides (ADR 0005 sibling of `sandbox_image`).

- `NULL` = inherit the instance default (`DEVCLAW_SANDBOX_MEMORY` /
  `DEVCLAW_SANDBOX_CPUS`). Tri-state writes via the existing `_UNSET`
  convention; `"inherit"` maps to `NULL` at the MCP edge.
- Registered in `_OVERRIDE_STR_FIELDS` → schema migration and
  `resolve_override` come free from the existing tuple-driven machinery.
- **Validation at the write choke point** (clarified with Denys — reject, not
  defer): `sandbox_memory` must match the docker/mem-parser grammar
  (`^[0-9]+[bkmg]?$`, case-insensitive suffix, parseable by
  `host_resources._parse_mem`) AND satisfy
  `parse(value) + COGNITION_MEM_RESERVE_BYTES <= host MemTotal`; the
  rejection message names both numbers. `sandbox_cpus` must parse as a
  positive float. Validation lives in `project_registry.py` beside
  `_validate_sandbox_image` and is exercised by both write edges.

## Value contracts (not persisted)

### Effective sandbox sizing

`(memory, cpus)` resolved once per launch:
`registry override → instance default`. Single source of truth for three
consumers, all fed the SAME resolved values:

1. `docker run --memory/--memory-swap/--cpus` (enforcement)
2. `-e DEVCLAW_SANDBOX_MEMORY / -e DEVCLAW_SANDBOX_CPUS` (declaration, FR-007)
3. admission accounting (`_mem_can_launch(effective_bytes)`)

### OOM evidence (runner-local)

Read from cgroup v2 at two moments — agent-process death and runner exit:

- `oom_kill` delta from `/sys/fs/cgroup/memory.events`
- cap from `/sys/fs/cgroup/memory.max` (fallback: the declared
  `DEVCLAW_SANDBOX_MEMORY` env)

Unreadable cgroup files ⇒ no evidence ⇒ generic path (FR-004; degrade, never
crash).

## Marker grammar (queue classification contract)

Runner terminal error, when the agent session died AND `oom_kill` increased:

```
sandbox OOM-killed (cap=<cap>, oom_kill=<n>): <original agent error>
```

- `_SANDBOX_OOM_MARKER = "sandbox OOM-killed"` — matched with `in` (mid-string
  tolerant), same convention and ordering as `_PROMPT_TOO_LONG_MARKER`; the
  fast-fail branch must sit BEFORE the quota/`classify_failure` branch is
  consulted for retry, mirroring the existing branch order in
  `_run_and_settle`.
- Settle reason template (US1):
  `sandbox OOM at cap <cap> — the container memory limit was exhausted and the
  kernel killed the agent. Not auto-retried: the same attempt reproduces the
  kill. Remedies: raise sizing (per-project override or
  DEVCLAW_SANDBOX_MEMORY) or bound the verify workload (capped workers,
  serial runs).`
- Failure-class rule: `("sandbox_oom", ("sandbox oom-killed",))` in
  `state_store/rows.py` `_FAILURE_CLASS_RULES` (lower-cased matching, per
  that table's convention).

## State transitions (goal layer)

```
increment fails, class=sandbox_oom
  ├─ envcap_redispatches == 0 → dispatch adapted brief
  │     (brief: cap value + bound-tooling directive — replaces the generic
  │      "take a strictly smaller slice" advice for this class)
  │     envcap_redispatches := 1
  └─ envcap_redispatches >= 1 → phase=blocked
        blocked_kind = "mechanical:env_cap"
        blocked_on   = "sandbox OOM at cap <cap> after an adapted retry —
                        raise sizing for this project or shrink its verify
                        workload, then resume_goal"
productive settle → envcap_redispatches := 0
```

`mechanical:env_cap` is human-gated (like `mechanical:dispatch_cap`): the cap
does not clear on its own; `resume_goal` after the operator raises sizing is
the recovery verb. FR-003's honesty rule also patches the dispatch-cap
message: when zero dispatched actions produced a delivery, the block reason
carries the dominant terminal failure class instead of "review the open PRs".
