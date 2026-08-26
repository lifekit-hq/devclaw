# Research: Sandbox OOM Legibility and Prevention (spec 020)

All decisions grounded in a code sweep of the worktree at `364fddd` (post-#701).

## D1 — OOM evidence source: the in-sandbox runner reads cgroup v2 `memory.events`

**Decision**: The runner captures OOM evidence itself — it reads
`/sys/fs/cgroup/memory.events` (`oom_kill` counter) and `memory.max` when the
agent process dies and at exit, and embeds a structured marker in its error
result: `sandbox OOM-killed (cap=<memory.max human-readable>, oom_kill=<n>)`.
The engine and docker lifecycle are untouched.

**Rationale**: The 2026-08-26 incident proves the runner survives the agent's
OOM death — the runner is the process that retried and reported
"session/prompt failed". It is already inside the cgroup, so the evidence is
one file read, synchronous, race-free. The engine cannot do this:
`--rm` means the container is gone by the time `proc.wait()` returns
(`run_sandcastle` → `consume_runner_output` at `devclaw/engine/runner_io.py:77-88`),
so a post-exit `docker inspect` loses the race by construction.

**Alternatives considered**:
- *Drop `--rm`, engine inspects then removes*: changes the destroy-on-exit
  lifecycle contract (`sandcastle.py:21-27`), adds a docker call per task, and
  interacts with the orphan sweep; rejected as machinery for evidence the
  runner already has locally.
- *`docker events` watcher*: a new long-lived daemon seam; rejected.
- *Classify bare `exited 137 without a result line` as OOM*: exit 137 is any
  SIGKILL (wall-clock teardown kills too); violates FR-004's
  no-false-positives. The runner-itself-OOM-killed case therefore stays
  generic (accepted gap: the runner is a small Python process and, with D3's
  shield, the least likely victim after the workload).

## D2 — Classification and retry wiring: mirror the prompt-too-long pattern exactly

**Decision**:
- **In-task (queue attempts loop)**: a new marker constant
  `_SANDBOX_OOM_MARKER` in `devclaw/queue/settle.py`, fast-fail branch beside
  `_PROMPT_TOO_LONG_MARKER` (settle.py:1330-1341's shape): `mark_failed` with
  a reason naming the effective cap and both remedies, `return None` before
  the retry-continue arm. No second container for the identical attempt.
- **Failure-class bucketing**: new `("sandbox_oom", ("sandbox oom-killed",))`
  rule in `devclaw/state_store/rows.py` `_FAILURE_CLASS_RULES` so telemetry
  and the goal layer see the class.
- **Goal level (FR-002a, clarified with Denys)**: exactly ONE adapted
  re-dispatch. New persisted counter `envcap_redispatches: int` on
  `GoalStatus` (added across the five lockstep seams: `goal/models.py`,
  `goal/state.py` DDL, `goal/state_status.py` upsert/read,
  `goal/store/status.py` frontmatter, `goal/store/view_migration.py`), reset
  to 0 on a productive settle alongside `heal_attempts`
  (`tick_settle.py:264`). `_advance_brief` (`goal/tick.py:408-416`) branches
  on the OOM class: instead of the generic "take a strictly smaller slice"
  advice, the brief names the cap and directs bounded tooling (capped
  workers, serial runs). If `envcap_redispatches >= 1` and the class recurs,
  the goal blocks with `blocked_kind="mechanical:env_cap"` and a reason
  naming the cap — never "review the open PRs" when nothing was delivered.

**Rationale**: settle-path string-marker classification is the established,
tested pattern (four sibling classes); the counter-with-reset shape copies
`heal_attempts`/`donegate_rounds` precedents. The `rows.py` comment for
`context_overflow` already anticipates this split: deterministic at the queue
level, a fresh adapted session may legitimately take a different shape.

**Alternatives considered**: a structured `retriable: false` field on the
runner result (schema change across engine/queue for one class — the marker
convention is the house style); counting via `actions_dispatched` arithmetic
(overloaded meaning, refunded on productive settles — a dedicated counter is
legible).

## D3 — Supervisor shield: self-raised `oom_score_adj` on workload processes

**Decision**: two seams, both unprivileged (the container runs as `USER agent`,
uid 1000 — `.sandcastle/Dockerfile` — so scores can only be RAISED):
1. **Verify children**: `runner/runner.py` `_run_verify` (and `_run_one_hook`,
   `_mise_run`) gain a `preexec_fn` that writes `800` to
   `/proc/self/oom_score_adj` before exec.
2. **Agent tool children**: the agent's own Bash children are spawned by the
   agent process, which the runner cannot wrap — but they run through bash,
   and non-interactive bash sources `$BASH_ENV`. The sandbox image ships
   `/opt/devclaw/oom-shield.sh` (`echo 800 > /proc/self/oom_score_adj
   2>/dev/null || true`), and the runner sets `BASH_ENV` in the agent's env
   allowlist (`runner/runner.py:1302-1323`). Every bash the agent spawns
   self-raises; children inherit the raised score.

The runner and agent stay at the default score; on memory exhaustion the
kernel's badness calculation (adj is ~percent-of-memory weighted) selects the
workload deterministically. The workload's death surfaces as ordinary
"Killed" command output the agent reads — US2's recoverable signal.

**Rationale**: zero new privileges (FR-006), zero vendor wiring (BASH_ENV is
a bash mechanism, agent-agnostic — Principle II holds: any ACP agent spawning
bash children gets the same shield), inert on the happy path.

**Alternatives considered**: `--cap-add SYS_RESOURCE` + negative adj on
runner/agent (new privilege — rejected by FR-006); nested cgroup with its own
`memory.max` for the workload subtree (needs cgroup delegation into the
container — docker doesn't provide it unprivileged); `ulimit -v` (rejected in
spec — breaks JVM/node allocators). A PATH-shim wrapping `bash` was rejected
as fragile (execve of absolute paths bypasses it) where `BASH_ENV` is exact.

## D4 — Cage visibility: the engine declares what it enforced

**Decision**: `_build_docker_args` adds a third env-forward family (the
docstring at `sandcastle.py:585-592` demands this be an explicit decision —
this is it): `-e DEVCLAW_SANDBOX_MEMORY=<effective> -e
DEVCLAW_SANDBOX_CPUS=<effective>`, sourced from the SAME values passed to
`--memory`/`--cpus` (single source, FR-007 — the values are the launch
parameters, never re-derived). The runner forwards both into the agent's env
allowlist. Worker guidance lands in
`runner/skills/_writes-code/40-verify-iterate.md` (the file that owns running
project tooling): bound worker counts/heap by the declared allocation;
bounded-memory-first, wall-clock second (operator ruling 2026-08-26). The
same guidance names the trap: `/proc/meminfo` and `nproc` report the HOST.

**Alternatives considered**: worker brief text instead of env (per-dispatch
duplication; env is the environment-as-instruction shape); reading
`/sys/fs/cgroup/memory.max` from the skill (true but model-hostile — an env
var is directly visible; the runner still reads cgroup files for D1 evidence).

## D5 — Per-project sizing: mirror `sandbox_image` end to end

**Decision**: registry fields `sandbox_memory` + `sandbox_cpus` appended to
`_OVERRIDE_STR_FIELDS` (`project_registry.py:60-63` — migration is automatic
from the tuple); validator `_validate_sandbox_memory` at the same write choke
point as `_validate_sandbox_image`, checking (a) grammar parseable by the
admission parser (`_parse_mem`), and (b) **write-time admittability
(clarified with Denys)**: `value + DEVCLAW_COGNITION_MEM_RESERVE ≤ host
MemTotal` — rejected loudly with the host budget in the message. MemTotal
(stable), not MemAvailable (fluctuates), defines "can never admit".
Resolution at launch copies `TaskQueue._sandbox_image`
(`task_queue.py:561-570`): new `_sandbox_sizing(project_id)` → new
`EngineRequest.sandbox_memory`/`sandbox_cpus` → `_build_docker_args` kwargs
(defaulting to the module globals). Admission
(`queue/admission.py:151-179`) parameterizes `_mem_can_launch`/
`_mem_commit_launch` with the per-task effective bytes, resolved in the pump
loop (`task_queue.py:511-515`) which already holds the row's `project_id`.
Write edges: `update_project` MCP tool + the console route's
`_OVR_FREE_STR`, mirroring `sandbox_image` exactly (including the
`"inherit"` → `None` mapping). `register_project` keeps its existing
minimalism (it does not expose `sandbox_image` today either); the asymmetry
is pre-existing and out of scope. Doctor: per spec-016 FR-014 the registry
schema change ships `checks_instance` coverage — stored sizing overrides
parse and remain admittable on THIS host.

**Alternatives considered**: sizing in `devclaw.json` in-repo config (sizing
is host capacity policy, operator-owned — the registry is the operator
surface; the repo cannot know the box); a single combined "profile" field
(small/large) — premature abstraction over two plain values.

## D6 — Program-path gap (recorded, bounded)

The program scheduling branch (`task_queue.py:530`) does not gate on memory
today. Spec 020 does not add it (pre-existing gap, `[P]` fan-out is behind
`DEVCLAW_FANOUT` default OFF); the plan notes it so the per-project
resolution lands where the program path can adopt it when fan-out arms.
