# Task execution flow — every hop, top to bottom

A step-by-step trace of what happens when devclaw runs ONE task (an
`implement_feature`, `fix_bug`, `review_repository`, etc.). Sibling
reference to [`decisions/0001-openhands-engine.md`](../decisions/0001-openhands-engine.md): that file
describes the *structural* choice (why three layers); this one walks the
*temporal* sequence (what each layer does, in order, for one task).

Use this when:
- a task fails and the symptom is ambiguous — find which step's column in
  the "fails if" rail matches the symptom;
- onboarding a reader who needs to know *what code runs when*;
- proposing a change — pin the step you're touching so the blast radius is
  scoped to one row.

The three nodes (Node 1 = openclaw waiter / TS, Node 2 = devclaw-mcp /
Python, Node 3 = ephemeral sandbox / per task) are defined in
[`decisions/0001-openhands-engine.md`](../decisions/0001-openhands-engine.md) and in
`~/memory/projects/devclaw/architecture.md`. This doc assumes you've read
one of those.

## The full sequence

```
TIME │  ACTOR / NODE                      │  WHAT HAPPENS                                   │  FAILS IF
─────┼────────────────────────────────────┼─────────────────────────────────────────────────┼──────────────────────
  ●  │  Denys (chat / voice / Telegram)   │  "build the read-only finance-sentry MCP"       │
     │                                    │                                                 │
  │  │  ┌─────────────────────────────┐                                                     │
  ▼  │  │  NODE 1 — openclaw waiter   │                                                     │
  ●  │  │  (Node.js, MCP CLIENT)      │                                                     │
     │  │                             │                                                     │
     │  │  Translates chat into:      │                                                     │
     │  │    create_goal(             │                                                     │
     │  │      objective="...",       │                                                     │
     │  │      verify_cmd="dotnet     │                                                     │
     │  │        test ...",           │                                                     │
     │  │      workspace_dir="..." )  │                                                     │
     │  │                             │                                                     │
     │  │  Sends JSON-RPC over MCP    │  ──────► HTTP POST /mcp                             │
     │  └─────────────────────────────┘                                                     │
     │                                                                                      │
     │  ┌─────────────────────────────────────────────────────────────┐                     │
     │  │  NODE 2 — devclaw-mcp  (Python, MCP SERVER + orchestrator)  │                     │
     │  │  long-lived; runs as `node`; needs docker.sock + GID 990    │  ◄── perm-denied if .env
     │  │                                                             │      LIFEKIT_DOCKER_GID
     │  │  Step A — @mcp.tool create_goal runs:                       │      missing (2026-06-25 bug)
     │  │     • admission: reject a saga with an unfilled slot,       │
     │  │       naming it (out_of_scope/invariants/established,       │
     │  │       spec 012 US2 — [] declares one empty, omitting        │
     │  │       one is a rejection, never a silent default)           │
     │  │     • write /var/lib/devclaw/goals/<id>/goal.yaml (facts,   │
     │  │       incl. the authored saga slots) + the first            │
     │  │       goal_status SQLite row                                │
     │  │       (STATUS.md is rendered alongside as a generated view) │
     │  │     • lifecycle="executing", phase="idle"                   │
     │  │     • return {goal_id, ...} to the waiter                   │
     │  │                                                             │
     │  │  Step B — heartbeat (15-min loop in goal/service.py):       │
     │  │     • read goal_status + unread goal_steering rows          │
     │  │       (SQLite, cheap, 0 tokens — STATUS.md/inbox.md are     │
     │  │       the generated views of the same rows, since T1)       │
     │  │     • if phase==idle and no in_flight:                      │
     │  │         advance_brief.py builds the mechanical   │          │
     │  │           advance brief (the saga framing's      │          │
     │  │           five named slots, spec 012 US2 —       │          │
     │  │           re-sent in full and size-bounded —     │          │
     │  │           + steering + settle detail + what      │          │
     │  │           prior increments of this goal          │          │
     │  │           delivered, spec 012 US1;               │          │
     │  │           ZERO LLM — the worker plans            │          │
     │  │           in-sandbox via speckit, spec 008)      │          │
     │  │     • dispatches the next advance task           │          │
     │  │                                                  │ Pro/Max OAuth (~/.claude on host),
     │  │  Step C — engine/sandcastle.run_sandcastle():    │ no API key
     │  │     • _validate_workspace(workspace_dir)          │                                │ PR #117: empty dir → fail-fast
     │  │     • host_bind = _translate_workspace_path(     │                                 │
     │  │         workspace_dir )                          │                                 │
     │  │     • docker run --rm --name devclaw-XXXX        │ ──┐                             │
     │  │         -v <host_bind>:/workspace                │   │                             │ docker run fails here
     │  │         -v devclaw-toolchains-<proj>:            │   │                             │ if Node 2 lacks GID 990
     │  │            /home/agent/.local/share/mise         │   │  per-project toolchain      │
     │  │         -v ~/.claude/.credentials.json:RO        │   │  cache (ADR 0005)           │
     │  │         -v <pre-trusted .claude.json>:RO         │   │
     │  │         --tmpfs /home/agent/.claude/session-env  │   │
     │  │         --network host                           │   │
     │  │         devclaw-sandbox:local                    │   │
     │  │         '<JSON payload>'                         │   │
     │  └──────────────────────────────────────────────────┘   │                             │
     │                                                         │                             │
     │  ┌──────────────────────────────────────────────────┐   │                             │
     │  │  NODE 3 — ephemeral sandbox (per task)           │   ▼                             │
     │  │  devclaw-XXXXXXXX  — runs as `agent`             │                                 │
     │  │  ENTRYPOINT: python /opt/devclaw/runner.py       │                                 │
     │  │  NO docker.sock. NO host personal dirs.          │                                 │
     │  │                                                  │                                 │
     │  │  Step D — runner.py:                             │                                 │
     │  │     • read JSON payload from argv                │                                 │
     │  │     • provision project-declared toolchain       │                                 │ toolchain_provision_failed:
     │  │       (ADR 0005): detect .mise.toml/.tool-       │                                 │ declared toolchain that
     │  │       versions (or translate global.json /       │                                 │ can't be provisioned fails
     │  │       package.json engines), `mise install`,     │                                 │ CLOSED here, before the
     │  │       export env so agent + verify gate inherit  │                                 │ agent spends anything
     │  │       it; no declaration → zero-cost no-op       │                                 │
     │  │     • build wrapped_goal (skills + quality bar   │                                 │
     │  │       + the task body — which for a goal-        │                                 │
     │  │       dispatched task carries the mechanical     │                                 │
     │  │       advance brief inside the goal — +          │                                 │
     │  │       a structured return contract as the last   │                                 │
     │  │       section, _wrap_goal)                       │                                 │
     │  │     • acp = _load_acp_client()  # sibling module │                                 │
     │  │     • client = acp.AcpClient(                    │                                 │
     │  │           acp_command, # DEVCLAW_ACP_COMMAND     │                                 │
     │  │           acp_env,     # allowlist + model tier  │                                 │
     │  │           on_event=_emit_event)                  │                                 │
     │  │     • client.run("/workspace", wrapped_goal)     │                                 │
     │  │       (initialize → session/new → session/prompt)│                                 │
     │  │                     ──────► spawns subprocess:  │                                  │
     │  │                                                  │                                 │
     │  │     ┌──────────────────────────────────────┐     │                                 │
     │  │     │  claude-agent-acp  (a binary)        │     │                                 │
     │  │     │  └─ claude  (Claude Code CLI)        │     │                                 │
     │  │     │     │                                │     │                                 │
     │  │     │     │ Uses ITS OWN tools — Bash,     │     │                                 │
     │  │     │     │ Read, Edit, Write, Grep —      │     │                                 │
     │  │     │     │ never client-provided ones     │     │                                 │
     │  │     │     │ (the runner advertises no fs/  │     │                                 │
     │  │     │     │ terminal capability).          │     │                                 │
     │  │     │     │                                │     │                                 │
     │  │     │     │ Reads /workspace/CLAUDE.md +   │     │                                 │
     │  │     │     │ AGENTS.md, plans, edits files  │     │                                 │
     │  │     │     │ via Bash + Write, runs         │     │                                 │
     │  │     │     │ commands inside /workspace.    │     │                                 │
     │  │     │     │                                │     │                                 │
     │  │     │     │ Streams session/update back    │     │                                 │
     │  │     │     │ to the runner's AcpClient via  │     │                                 │
     │  │     │     │ JSON-RPC over stdio.           │     │                                 │
     │  │     │     │                                │     │                                 │
     │  │     │     │ Finishes turn → stopReason.    │     │                                 │
     │  │     └──────────────────────────────────────┘     │                                 │
     │  │                                                  │                                 │
     │  │  Step E — every event from claude:               │                                 │
     │  │     on_event callback writes one line:           │                                 │
     │  │       event: {"id":..., "type":..., ...}         │  ──► stdout (devclaw-mcp reads) │
     │  │                                                  │                                 │
     │  │  Step F — when conversation.run() returns:       │                                 │
     │  │     verify = subprocess.run(verify_cmd,          │                                 │
     │  │         cwd="/workspace", timeout=…)             │  ◄── must succeed (exit 0)      │ PR #117 sibling needed:
     │  │     # this is `dotnet test ...` for finance-sentry                                 │ pre-check `command -v dotnet`
     │  │                                                  │                                 │ in the sandbox before
     │  │  Step G — emit terminal line:                    │                                 │ spawning, fail-fast if missing
     │  │     result: {"status":"ok","verify":{...},       │                                 │ (the dotnet-not-found bug)
     │  │              "message":"..."}                    │  ──► stdout                     │
     │  └──────────────────────────────────────────────────┘                                 │
     │                  │                                                                    │
     │       container  │  exits → docker --rm vaporizes it.  Tmpfs gone, scratch gone.      │
     │                  ▼                                                                    │
     │  ┌─────────────────────────────────────────────────────────────┐                      │
     │  │  Back in NODE 2 — devclaw-mcp (consume_runner_output):      │                      │
     │  │                                                             │                      │
     │  │  Step H — parse the stream:                                 │                      │
     │  │     • event: lines → on_event callback (logging, console) │                      │
     │  │     • final result: line → EngineResult                     │                      │
     │  │     • if no result line → "sandbox exited 1 without a       │                      │
     │  │       result line" (the misleading error string)            │                      │
     │  │                                                             │                      │
     │  │  Step I — review gate (quality/review_gate):                │                      │
     │  │     • git diff main..HEAD of the workspace                  │                      │
     │  │     • feed diff to `claude --print` for adversarial check   │                      │
     │  │     • + workspace snapshot as REPOSITORY CONTEXT (#227)     │                      │
     │  │     • single adversarial reviewer over the diff,            │                      │
     │  │       wrapped in the cognition-timeout degrade ladder       │                      │
     │  │       (oversized diff → per-file split + union)             │                      │
     │  │     • test-integrity guard: were tests deleted/weakened?    │                      │
     │  │     • either: ok / needs revision (kicked back to engineer) │                      │
     │  │                                                             │                      │
     │  │  Step J — delivery (delivery.deliver_change):               │                      │
     │  │     • git push to branch goal/<slug>                        │                      │
     │  │     • gh pr create  (conventional commit + diffstat body)   │                      │
     │  │     • record PR URL                                         │                      │
     │  │                                                             │                      │
     │  │  Step K — append a goal_deliveries row (grounded evidence   │                      │
     │  │     for the direction evaluator; deliveries.md mirrors it). │                      │
     │  │     Transition goal_status: in_flight=null,                 │                      │
     │  │     last_progress_at=now (STATUS.md view rewritten after).  │                      │
     │  │                                                             │                      │
     │  │  Step L — notify (notify_url POST to notify-relay:8090):    │                      │
     │  │     → Telegram message to Denys with PR link + gate verdict │                      │
     │  └────────────────────────────┬────────────────────────────────┘                      │
     │                               │                                                       │
     │                               ▼                                                       │
     │                       Denys's phone buzzes                                             │
     │                                                                                       │
     ▼  back to the heartbeat — next tick decides: more work, direction-eval, or done-gate.  │
```

## How the 2026-06-25 cascade maps onto these steps

| Failure | Step | Symptom seen upstream | Real fix |
|---|---|---|---|
| Empty workspace bind | Step C — `_translate_workspace_path` passed an out-of-prefix path → host bind was empty | Sandbox starts, claude finds empty repo, hits wall-clock | PR #117 — `_validate_workspace()` in Step C |
| Wrong sandbox image | Step C — an image without the needed SDK and no toolchain declaration in the repo | Sandbox runs, claude writes scaffold OK, **Step F** `dotnet test` exits 127 | Declare the toolchain in the repo (`global.json` / `.mise.toml`) — the Step-D pre-step provisions it (ADR 0005; gate passed live 2026-07-21, finance-sentry#291 on the lean image). Escape hatch for a stack mise can't provision: the project's `sandbox_image` registry override |
| Docker GID drift | **Before** Step C — the very `docker run` in Step C never succeeds | Permission denied at `/var/run/docker.sock`, "sandbox exited 1 / no result line" — looks identical to Step H's "no terminal result" | `compose/.env` with `LIFEKIT_DOCKER_GID=990`; also: a fail-fast precheck in run_sandcastle that exec's `docker version` once on startup |

All three were silent-timeout failures because the engine output in Step H
couldn't distinguish *"the docker run from Step C never happened"* from
*"the sandbox started but exited 1"*.

## Open improvements (queue, not yet built)

1. **Failure-mode disambiguation in Step H.** Distinct error strings for:
   "the `docker run` in Step C never returned a container ID" vs "the
   sandbox started, ran, and exited 1" vs "the sandbox produced no
   `result:` line." All three look identical today; the operator has to
   guess and usually picks the wrong fix.
2. **Toolchain precheck (Step C, sibling of PR #117).** Before `docker
   run`, exec `command -v <first-token-of-verify_cmd>` inside the sandbox
   image and refuse to spawn if missing. Same shape as `_validate_workspace`.
3. **Healthcheck verifies docker access (devclaw-mcp service).** Today's
   healthcheck is `curl /health`; doesn't catch the GID drift. Add a
   `docker version` to the test so a broken-but-running orchestrator flips
   unhealthy in 30s instead of being discovered by the first task.
4. **Sandbox image selection by detected stack.** SUPERSEDED by
   [ADR 0005](../decisions/0005-generic-sandbox-toolchain.md): there is no
   per-stack image to select — one lean image, and the toolchain is a
   project-declared fact provisioned by mise in the runner pre-step (Step D).
   (Historically we planned to reuse a `_detect_stack()` helper to pick the
   image per task; it was deleted with the `setup_cicd` scaffolder — its
   5-stack template list was silently wrong for fullstack repos.)
5. **AGENTS.md posture block for sandbox.** Generated `AGENTS.md` should
   prepend "you're in a docker-less sandbox; ignore stack-bring-up
   instructions from `CLAUDE.md`; use the unit-only verify path." Today
   the agent has to figure it out from instruction overrides.

## The direct-task path — branch targeting (ADR 0011)

Everything above traces a task the GOAL layer dispatched. The same sequence
also runs for a **direct task** (`dispatch_task` at layer 1 → `queue.submit`
→ Steps C–K) with no goal and no heartbeat — the v1-helper path
re-surfaced by [ADR 0011](../decisions/0011-branch-target-delivery-seam.md).
Two optional inputs shape ONLY Step J (delivery) and a new pre-step:

- `base_branch` — grounds the ahead-count/diff range and becomes
  `gh pr create --base <base_branch>`. Verified resolvable at dispatch;
  a bogus base fails the task loudly before the engine runs.
- `target_branch` — the queue preps the workspace ON that branch
  (`prepare_workspace(branch=target_branch)`, created off `base_branch` if
  new), and Step J reuses its single PR (the widened goal-mode reuse path).
  If delivery lands anywhere else, the task settles `failed` — the
  "continue this branch" contract never silently degrades.

Omitted ⇒ byte-identical legacy behavior (fresh derived branch → default
base). Goal-dispatched tasks pass neither.
